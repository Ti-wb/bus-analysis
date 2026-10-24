import type { CSSProperties, ReactNode } from 'react';
import type { VillageAges } from '../age';
import { AgeControls, AgeLegend } from './VillageAge';
import { ALL_DAY, COLORS, COVERAGE_ALPHA, COVERAGE_LAYERS, DAY_LABELS, GAP_COLORS, NEED_BANDS, NEED_COLORS, QUADRANTS, STATION_TYPES, type Quadrant } from '../config';
import { distanceText } from '../coverage';
import { hourText, scaleFor } from '../data';
import { isDefaultWeights } from '../need';
import type { AppState, CoverageLayer } from '../types';

interface Props {
  villageAges: VillageAges | null;
  ageError: Error | null;
  onRetryAges: () => void;
  selectedVillage: string | null;
  onVillage: (id: string | null) => void;
  state: AppState;
  playing: boolean;
  needCounts: Record<Quadrant, number> | null;
  onChange: (patch: Partial<AppState>) => void;
  onTogglePlay: () => void;
  onPreferences: () => void;
}

interface SummaryRow {
  key: string;
  label: string;
  value: string;
  color: string;
  off?: boolean;
}

const rgba = (layer: CoverageLayer, alpha = 1) => `rgba(${COVERAGE_LAYERS[layer].rgb.join(',')},${alpha})`;

/** Walking radius text: one value when every counted type shares it, else the range. */
function walkText(state: AppState): string {
  const radii = STATION_TYPES.filter((t) => state.walkTypes.has(t.key)).map((t) => state.walkRadii[t.key]);
  if (!radii.length) return '未選站點';
  const [low, high] = [Math.min(...radii), Math.max(...radii)];
  return low === high ? distanceText(low) : `${low}–${high} m`;
}

function coverageRows(state: AppState): SummaryRow[] {
  return (['walk', 'ride'] as CoverageLayer[]).map((layer) => ({
    key: layer,
    label: COVERAGE_LAYERS[layer].label,
    value: layer === 'walk' ? walkText(state) : distanceText(state.rideRadius),
    color: rgba(layer),
    off: !state.coverage.has(layer),
  }));
}

function frequencyRows(state: AppState): SummaryRow[] {
  return [
    { key: 'grid', label: '公車班次網格', value: DAY_LABELS[state.day], color: COLORS[5], off: !state.layers.has('grid') },
  ];
}

function needRows(state: AppState): SummaryRow[] {
  const total = state.weights.reduce((a, b) => a + b, 0) || 1;
  return [
    { key: 'band', label: '時段', value: NEED_BANDS.find((b) => b.key === state.band)!.label, color: NEED_COLORS[2][0] },
    { key: 'view', label: '呈現方式', value: state.needView === 'quadrant' ? '需求 × 供給' : '落差值', color: NEED_COLORS[2][2] },
    { key: 'weights', label: '需求權重', value: state.weights.map((w) => Math.round((w / total) * 100)).join('／'), color: NEED_COLORS[1][1] },
  ];
}

/** Settings live in the preferences sidebar; this card only summarises them. */
function Summary({ rows, foot, onPreferences, children }: { rows: SummaryRow[]; foot: string; onPreferences: () => void; children?: ReactNode }) {
  return (
    <div className="coverage-summary">
      <h2>目前顯示</h2>
      {rows.map(({ key, label, value, color, off }) => (
        <div key={key} className={`summary-row${off ? ' is-off' : ''}`} style={{ '--c': color } as CSSProperties}>
          <i />{label}
          <b>{off ? '關閉' : value}</b>
        </div>
      ))}
      {children}
      <div className="summary-foot">
        <span>{foot}</span>
        <button className="link-btn" onClick={onPreferences}>到「偏好」調整</button>
      </div>
    </div>
  );
}

function CoverageLegend({ state }: { state: AppState }) {
  const layers = (Object.keys(COVERAGE_LAYERS) as CoverageLayer[]).filter((layer) => state.coverage.has(layer));
  return (
    <section className="card legend">
      <h3>同一張網格，兩種移動範圍</h3>
      {layers.map((layer) => (
        <div key={layer} className="coverage-legend">
          <p>{COVERAGE_LAYERS[layer].label} · {layer === 'walk' ? walkText(state) : distanceText(state.rideRadius)}</p>
          <div className="legend-scale legend-scale-5">
            {COVERAGE_ALPHA.map((a) => <span key={a} style={{ background: rgba(layer, a) }} />)}
          </div>
          <div className="legend-ends"><span>{layer === 'walk' ? '近站' : '站心'}</span><span>外圍</span></div>
        </div>
      ))}
      {!layers.length && <p className="muted">兩個覆蓋圖層都已關閉。</p>}
    </section>
  );
}

function NeedLegend({ state, counts }: { state: AppState; counts: Record<Quadrant, number> | null }) {
  return (
    <section className="card legend">
      {state.needView === 'quadrant' ? (
        <>
          <h3>需求 × 供給（同時段排名三等分）</h3>
          <div className="bivariate">
            <span className="bivariate-y">需求 →</span>
            <div className="bivariate-grid">
              {[2, 1, 0].map((d) => [0, 1, 2].map((s) => <span key={`${d}${s}`} style={{ background: NEED_COLORS[d][s] }} />))}
            </div>
            <span className="bivariate-x">供給 →</span>
          </div>
          <ul className="quadrant-list">
            {(Object.keys(QUADRANTS) as Quadrant[]).map((q) => (
              <li key={q}><b>{QUADRANTS[q].label}</b> {QUADRANTS[q].note}{counts && <span className="muted"> · {counts[q]} 格</span>}</li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <h3>落差＝需求排名 − 供給排名</h3>
          <div className="legend-scale legend-scale-5">{GAP_COLORS.map((c) => <span key={c} style={{ background: c }} />)}</div>
          <div className="legend-ends"><span>供給多於需求</span><span>需求多於供給</span></div>
        </>
      )}
      {isDefaultWeights(state.weights)
        ? <p className="legend-note"><span className="robust-swatch" />深框＝權重 ±20% 抽樣 1,000 次，八成以上落在優先前 5%</p>
        : <p className="legend-note">已調整權重，穩健外框只適用預設權重，暫不顯示</p>}
      <p className="legend-note"><span className="hatch-swatch" />斜線＝附近有附線缺班表，供給是下限</p>
      <p className="legend-note"><span className="scope-swatch" />淡灰＝人口少於 100 且無主要地點，不排名</p>
    </section>
  );
}

export default function Panel({ villageAges, ageError, onRetryAges, selectedVillage, onVillage, state, playing, needCounts, onChange, onTogglePlay, onPreferences }: Props) {
  const { labels } = scaleFor(state);
  const frequency = state.mode === 'frequency';
  return (
    <aside className="panel">
      <section className="card intro">
        <h1>城市裡的連結，<br />一眼看見。</h1>
        <p className="muted">公車、捷運與 YouBike 的路網與覆蓋。點選站點，認識周邊人口與設施。</p>

        <button className="age-entry link-btn" onClick={() => onChange({ mode: state.mode === 'village_age' ? 'coverage' : 'village_age' })}>{state.mode === 'village_age' ? '返回站點覆蓋' : '查看村里年齡'}</button>
        {state.mode === 'village_age' && <AgeControls state={state} data={villageAges} error={ageError} onChange={onChange} onRetry={onRetryAges} selectedVillage={selectedVillage} onVillage={onVillage} />}

        {state.mode === 'coverage' && <Summary rows={coverageRows(state)} foot={`${state.coverageCell} m 網格`} onPreferences={onPreferences} />}
        {state.mode === 'frequency' && (
          <Summary rows={frequencyRows(state)} foot={`${state.cell} m 網格`} onPreferences={onPreferences}>
            <div className="hour-row">
              <button className={`icon-btn${playing ? ' is-playing' : ''}`} title="播放 24 小時" aria-label={playing ? '暫停' : '播放'} onClick={onTogglePlay}>
                {playing ? '❚❚' : '▶'}
              </button>
              <input type="range" min={0} max={ALL_DAY} step={1} value={state.hour} aria-label="小時" onChange={(e) => onChange({ hour: Number(e.target.value) })} />
            </div>
            <div className="hour-label">
              <span className="hour-text">{hourText(state.hour)}</span>
              <span className="muted">拉到最右為全日</span>
            </div>
          </Summary>
        )}
        {state.mode === 'need' && <Summary rows={needRows(state)} foot="250 m 網格" onPreferences={onPreferences} />}
      </section>

      {frequency ? (
        <section className="card legend">
          <h3>{state.hour === ALL_DAY ? '全日' : '每小時'}計畫發車數（每 {state.cell} m 格）</h3>
          <div className="legend-scale">
            {labels.map((label, i) => (
              <div key={label}><span style={{ background: COLORS[i] }} />{label}</div>
            ))}
          </div>
          <p className="legend-note"><span className="hatch" />斜紋＝格內附線都沒有班表（資料缺漏，非 0）</p>
          <p className="legend-note"><span className="nostop" />淡色＝格內沒有公車站牌</p>
        </section>
      ) : state.mode === 'village_age' ? <AgeLegend data={villageAges} groupKey={state.ageGroup} ageSet={state.ageSet} /> : state.mode === 'need' ? <NeedLegend state={state} counts={needCounts} /> : <CoverageLegend state={state} />}
    </aside>
  );
}
