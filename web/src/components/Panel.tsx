import type { CSSProperties } from 'react';
import { ALL_DAY, CELL_SIZES, COLORS, COVERAGE_ALPHA, COVERAGE_LAYERS, DAY_LABELS, DEFAULT_NEED_WEIGHTS, GAP_COLORS, NEED_BANDS, NEED_COLORS, NEED_WEIGHT_LABELS, PLACE_CATEGORIES, QUADRANTS, STATION_TYPES, type Quadrant } from '../config';
import { distanceText } from '../coverage';
import { hourText, scaleFor } from '../data';
import { isDefaultWeights } from '../need';
import type { AnalysisMode, AppState, CoverageLayer, DayType, LayerGroup, NeedView, NeedWeights } from '../types';

interface Props {
  state: AppState;
  playing: boolean;
  needCounts: Record<Quadrant, number> | null;
  onChange: (patch: Partial<AppState>) => void;
  onTogglePlay: () => void;
}

const MODES: { key: AnalysisMode; label: string }[] = [
  { key: 'frequency', label: '班次網格' },
  { key: 'coverage', label: '站點覆蓋' },
  { key: 'need', label: '需求落差' },
];

const NEED_VIEWS: { key: NeedView; label: string }[] = [
  { key: 'quadrant', label: '需求 × 供給' },
  { key: 'gap', label: '落差值' },
];

const LAYER_TOGGLES: { group: LayerGroup; label: string; swatch: string; note?: (s: AppState) => string }[] = [
  { group: 'grid', label: '公車班次網格', swatch: 'swatch-grid', note: (s) => `${s.cell} m` },
  { group: 'routes', label: '公車路線', swatch: 'swatch-bus' },
  { group: 'stops', label: '公車站牌', swatch: 'swatch-stop', note: () => '放大後顯示' },
  { group: 'mrt', label: '捷運路網', swatch: 'swatch-mrt' },
  { group: 'places', label: '重要地點', swatch: 'swatch-place', note: () => '點選類別篩選' },
  { group: 'youbike', label: 'YouBike 站點', swatch: 'swatch-youbike', note: () => '放大後顯示' },
];

const RADIUS_KEY = { walk: 'walkRadius', ride: 'rideRadius' } as const;

function toggled<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

const rgba = (layer: CoverageLayer, alpha = 1) => `rgba(${COVERAGE_LAYERS[layer].rgb.join(',')},${alpha})`;

function CoverageControls({ state, onChange }: Pick<Props, 'state' | 'onChange'>) {
  return (
    <>
      <h2>覆蓋圖層</h2>
      {(Object.keys(COVERAGE_LAYERS) as CoverageLayer[]).map((layer) => {
        const spec = COVERAGE_LAYERS[layer];
        const on = state.coverage.has(layer);
        const radius = state[RADIUS_KEY[layer]];
        return (
          <div key={layer} className={`coverage-card${on ? ' is-on' : ''}`} style={{ '--c': rgba(layer), '--c-soft': rgba(layer, 0.08) } as CSSProperties}>
            <label className="coverage-head">
              <i />
              <b>{spec.label}</b>
              <input type="checkbox" role="switch" checked={on} onChange={() => onChange({ coverage: toggled(state.coverage, layer) })} />
            </label>
            <p className="muted">{layer === 'walk' ? '從公車、捷運、YouBike 站向外擴散' : spec.note}</p>
            <div className="segmented" role="radiogroup" aria-label={`${spec.label}半徑`}>
              {spec.radii.map((r) => (
                <button key={r} role="radio" aria-checked={radius === r} className={radius === r ? 'is-active' : ''} onClick={() => onChange({ [RADIUS_KEY[layer]]: r })}>
                  {distanceText(r)}
                </button>
              ))}
            </div>
            {layer === 'walk' && (
              <div className="place-chips station-chips">
                {STATION_TYPES.map((t) => (
                  <button
                    key={t.key}
                    className={`place-chip${state.walkTypes.has(t.key) ? ' is-on' : ''}`}
                    style={{ '--c': rgba('walk') } as CSSProperties}
                    aria-pressed={state.walkTypes.has(t.key)}
                    onClick={() => onChange({ walkTypes: toggled(state.walkTypes, t.key) })}
                  >
                    <i />{t.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

function CoverageLegend({ state }: { state: AppState }) {
  const layers = (Object.keys(COVERAGE_LAYERS) as CoverageLayer[]).filter((layer) => state.coverage.has(layer));
  return (
    <section className="card legend">
      <h3>由站心向外，逐格變淡</h3>
      {layers.map((layer) => (
        <div key={layer} className="coverage-legend">
          <p>{COVERAGE_LAYERS[layer].label} · {distanceText(state[RADIUS_KEY[layer]])}</p>
          <div className="legend-scale legend-scale-5">
            {COVERAGE_ALPHA.map((a) => <span key={a} style={{ background: rgba(layer, a) }} />)}
          </div>
          <div className="legend-ends"><span>站心</span><span>外圍</span></div>
        </div>
      ))}
      {!layers.length && <p className="muted">兩個覆蓋圖層都已關閉。</p>}
      <p className="legend-note">每格約 50 m；直線半徑，未計路網與河川阻隔。</p>
    </section>
  );
}

function NeedControls({ state, onChange }: Pick<Props, 'state' | 'onChange'>) {
  const total = state.weights.reduce((a, b) => a + b, 0) || 1;
  const setWeight = (k: number, value: number) => {
    const weights = [...state.weights] as NeedWeights;
    weights[k] = value;
    if (weights.some((w) => w > 0)) onChange({ weights });
  };
  return (
    <>
      <h2>時段</h2>
      <div className="segmented" role="radiogroup" aria-label="時段">
        {NEED_BANDS.map((b) => (
          <button key={b.key} role="radio" aria-checked={state.band === b.key} title={b.label} className={state.band === b.key ? 'is-active' : ''} onClick={() => onChange({ band: b.key })}>
            {b.short}
          </button>
        ))}
      </div>
      <p className="hour-label muted">{NEED_BANDS.find((b) => b.key === state.band)!.label}</p>

      <h2>呈現方式</h2>
      <div className="segmented segmented-2" role="radiogroup" aria-label="呈現方式">
        {NEED_VIEWS.map((v) => (
          <button key={v.key} role="radio" aria-checked={state.needView === v.key} className={state.needView === v.key ? 'is-active' : ''} onClick={() => onChange({ needView: v.key })}>
            {v.label}
          </button>
        ))}
      </div>

      <h2 className="need-weights-head">
        需求權重
        {!isDefaultWeights(state.weights) && <button className="link-btn" onClick={() => onChange({ weights: DEFAULT_NEED_WEIGHTS })}>重設</button>}
      </h2>
      {NEED_WEIGHT_LABELS.map((label, k) => (
        <label key={label} className="need-weight">
          <span><i className={`need-part-${k}`} />{label}</span>
          <input type="range" min={0} max={100} step={5} value={state.weights[k]} aria-label={`${label}權重`} onChange={(e) => setWeight(k, Number(e.target.value))} />
          <b>{Math.round((state.weights[k] / total) * 100)}%</b>
        </label>
      ))}
      <p className="muted need-note">預設 50／30／20 是方法文件的假設值；總和自動換算成 100%。</p>
    </>
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

export default function Panel({ state, playing, needCounts, onChange, onTogglePlay }: Props) {
  const { labels } = scaleFor(state);
  const frequency = state.mode === 'frequency';
  return (
    <aside className="panel">
      <section className="card intro">
        <h1>城市裡的連結，<br />一眼看見。</h1>
        <p className="muted">公車、捷運與 YouBike 的路網與覆蓋。點選站點，認識周邊人口與設施。</p>

        <h2>分析圖層</h2>
        <div className="segmented" role="radiogroup" aria-label="分析圖層">
          {MODES.map((m) => (
            <button key={m.key} role="radio" aria-checked={state.mode === m.key} className={state.mode === m.key ? 'is-active' : ''} onClick={() => onChange({ mode: m.key })}>
              {m.label}
            </button>
          ))}
        </div>

        {state.mode === 'coverage' && <CoverageControls state={state} onChange={onChange} />}
        {state.mode === 'need' && <NeedControls state={state} onChange={onChange} />}

        <h2>顯示圖層</h2>
        {LAYER_TOGGLES.filter(({ group }) => frequency || group !== 'grid').map(({ group, label, swatch, note }) => (
          <div key={group}>
            <label className="layer-toggle">
              <input type="checkbox" checked={state.layers.has(group)} onChange={() => onChange({ layers: toggled(state.layers, group) })} />
              <span className={`swatch ${swatch}`} />
              {label}
              {note && <small>{note(state)}</small>}
            </label>
            {group === 'places' && state.layers.has('places') && (
              <div className="place-chips">
                {PLACE_CATEGORIES.map((c) => (
                  <button
                    key={c.key}
                    className={`place-chip${state.places.has(c.key) ? ' is-on' : ''}`}
                    style={{ '--c': c.color } as CSSProperties}
                    aria-pressed={state.places.has(c.key)}
                    onClick={() => onChange({ places: toggled(state.places, c.key) })}
                  >
                    <i />{c.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}

        {frequency && (
          <>
            <h2>網格大小</h2>
            <div className="segmented segmented-4" role="radiogroup" aria-label="網格大小">
              {CELL_SIZES.map((cell) => (
                <button key={cell} role="radio" aria-checked={state.cell === cell} className={state.cell === cell ? 'is-active' : ''} onClick={() => onChange({ cell })}>
                  {cell} m
                </button>
              ))}
            </div>

            <h2>時段</h2>
            <div className="segmented" role="radiogroup" aria-label="日型">
              {(Object.keys(DAY_LABELS) as DayType[]).map((day) => (
                <button key={day} role="radio" aria-checked={state.day === day} className={state.day === day ? 'is-active' : ''} onClick={() => onChange({ day })}>
                  {DAY_LABELS[day]}
                </button>
              ))}
            </div>
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
          </>
        )}
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
      ) : state.mode === 'need' ? <NeedLegend state={state} counts={needCounts} /> : <CoverageLegend state={state} />}
    </aside>
  );
}
