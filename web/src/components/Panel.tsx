import type { CSSProperties, ReactNode } from 'react';
import { ALL_DAY, COLORS, COVERAGE_ALPHA, COVERAGE_LAYERS, DAY_LABELS, ELDERLY_COLORS, GAP_COLORS, NEED_BANDS, NEED_COLORS, QUADRANTS, STATION_TYPES, VILLAGE_COLORS, type Quadrant } from '../config';
import { distanceText } from '../coverage';
import { hourText, scaleFor, type SupplementalKey } from '../data';
import { isDefaultWeights } from '../need';
import { snapshotPeriodText } from '../regional';
import type { AppState, CoverageLayer, LayerGroup, MapData } from '../types';

interface Props {
  state: AppState;
  playing: boolean;
  needCounts: Record<Quadrant, number> | null;
  data: MapData | null;
  supplementalPending: Set<SupplementalKey>;
  onRetrySupplemental: () => void;
  onChange: (patch: Partial<AppState>) => void;
  onTogglePlay: () => void;
  onPreferences: () => void;
}

const EXTRA_LAYERS: { group: LayerGroup; key: SupplementalKey; label: string; swatch: string }[] = [
  { group: 'regional_routes', key: 'regional_bus_routes', label: '新北／跳蛙路線', swatch: 'swatch-regional' },
  { group: 'regional_stops', key: 'regional_bus_stops', label: '新北／跳蛙站位', swatch: 'swatch-regional-stop' },
  { group: 'villages', key: 'villages', label: '里人口與年齡', swatch: 'swatch-village' },
  { group: 'traffic', key: 'traffic_sections', label: '道路速率快照', swatch: 'swatch-traffic' },
  { group: 'bus_times', key: 'bus_travel_times', label: '公車站間推估旅時', swatch: 'swatch-travel' },
];

function toggled<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

interface SummaryRow {
  key: string;
  label: string;
  value: string;
  color: string;
  off?: boolean;
}

export function ExtraControls({ state, data, supplementalPending, onChange, onRetrySupplemental }: Pick<Props, 'state' | 'data' | 'supplementalPending' | 'onChange' | 'onRetrySupplemental'>) {
  return <>
    <h3>跨市資料圖層</h3>
    <p className="layer-scope-note">資料快照；班次、覆蓋與需求模型範圍仍為臺北市。</p>
    {EXTRA_LAYERS.map(({ group, key, label, swatch }) => {
      const loaded = data?.[key];
      const failed = data?.supplemental_errors.some((message) => message.startsWith(key));
      return <label className="layer-toggle extra-layer-toggle" key={group}>
        <input type="checkbox" checked={state.layers.has(group)} onChange={() => onChange({ layers: toggled(state.layers, group) })} />
        <span className={`swatch ${swatch}`} />{label}
        <small>{supplementalPending.has(key) ? '載入中…' : failed ? '載入失敗' : loaded && 'features' in loaded ? `${loaded.features.length.toLocaleString()} 筆` : '套用後載入'}</small>
      </label>;
    })}
    {(state.layers.has('regional_routes') || state.layers.has('regional_stops')) && <label className="sub-layer-toggle">
      <input type="checkbox" checked={state.leapfrogOnly} onChange={(event) => onChange({ leapfrogOnly: event.target.checked })} />僅顯示官方類別核對的跳蛙公車
    </label>}
    {state.layers.has('villages') && <>
      <div className="segmented segmented-2" role="radiogroup" aria-label="里人口著色方式">
        {([{ key: 'population', label: '戶籍人口' }, { key: 'elderly_share', label: '65 歲以上占比' }] as const).map((metric) => <button key={metric.key} role="radio" aria-checked={state.villageMetric === metric.key} className={state.villageMetric === metric.key ? 'is-active' : ''} onClick={() => onChange({ villageMetric: metric.key })}>{metric.label}</button>)}
      </div>
      <p className="layer-scope-note">顯示整里統計；套用後暫藏分析網格，關閉此層即恢復。</p>
    </>}
    <ExtraStatus data={data} supplementalPending={supplementalPending} onRetrySupplemental={onRetrySupplemental} />
  </>;
}

function ExtraStatus({ data, supplementalPending, onRetrySupplemental }: Pick<Props, 'data' | 'supplementalPending' | 'onRetrySupplemental'>) {
  const loading = EXTRA_LAYERS.filter(({ key }) => supplementalPending.has(key)).map(({ label }) => label);
  return <>
    {!!loading.length && <p className="layer-scope-note" role="status">載入中：{loading.join('、')}。</p>}
    {!!data?.supplemental_errors.length && <div className="overlay-errors" role="alert">
      <p>{data.supplemental_errors.length} 份補充資料載入失敗，其他圖層仍可使用。</p>
      <button className="retry-button" onClick={onRetrySupplemental}>重試失敗的資料</button>
      <details><summary>查看原因</summary>{data.supplemental_errors.map((message) => <p key={message}>{message}</p>)}</details>
    </div>}
  </>;
}

function ExtraLegend({ state, data }: Pick<Props, 'state' | 'data'>) {
  const active = EXTRA_LAYERS.filter((layer) => state.layers.has(layer.group));
  if (!active.length) return null;
  const village = state.layers.has('villages');
  const trafficRecord = data?.supplemental_manifest?.records.find((record) => record.id === 'traffic_sections');
  const trafficTime = trafficRecord ? snapshotPeriodText({ ...trafficRecord, period: data?.traffic_sections?.metadata?.period ?? trafficRecord.period }) : '觀測快照時間未提供';
  return <section className="card legend extra-legend">
    <h3>跨市圖層圖例與快照</h3>
    {state.layers.has('regional_routes') && <><p className="legend-note"><span className="swatch swatch-regional" />新北路線 <span className="swatch swatch-frog" />跳蛙路線</p><p className="layer-scope-note">依站序連線的示意路網，不是實際道路軌跡。</p></>}
    {state.layers.has('regional_stops') && <p className="legend-note"><span className="swatch swatch-regional-stop" />新北站位在縮放 12 級以上顯示</p>}
    {village && <>
      <h4>{state.villageMetric === 'population' ? '每里戶籍人口（人）' : '每里 65 歲以上人口占比'}</h4>
      <div className="legend-scale legend-scale-5">{(state.villageMetric === 'population' ? VILLAGE_COLORS : ELDERLY_COLORS).map((color, index) => <div key={color}><span style={{ background: color }} />{(state.villageMetric === 'population' ? ['<2千', '2–4千', '4–8千', '8千–1.2萬', '≥1.2萬'] : ['<15%', '15–20%', '20–25%', '25–30%', '≥30%'])[index]}</div>)}</div>
      <p className="legend-note"><span className="swatch swatch-missing" />未核對或缺資料，非 0；點選里界查看年齡人數。</p>
    </>}
    {state.layers.has('traffic') && <>
      <h4>道路速率（km/h）</h4>
      <div className="legend-scale legend-scale-5">{['#c34e42', '#e59043', '#bcb751', '#398f81', '#8996a4'].map((color, index) => <div key={color}><span style={{ background: color }} />{['<15', '15–30', '30–50', '≥50', '無有效值'][index]}</div>)}</div>
      <p className="layer-scope-note">{trafficTime}；{data?.traffic_sections?.features.length ?? '—'} 個有起迄點的路段示意線，非即時。0 與來源負值保留為無有效值。</p>
    </>}
    {state.layers.has('bus_times') && <>
      <h4>{DAY_LABELS[state.day]} {hourText(state.hour)} · 站間推估旅時</h4>
      {state.hour === ALL_DAY ? <p className="layer-scope-note">全日以灰線顯示；點選查看有值整點的最小～最大值，不加總。</p> : <div className="legend-scale legend-scale-5">{['#71b6b1', '#3c9da4', '#327caf', '#625ca8', '#963c87'].map((color, index) => <div key={color}><span style={{ background: color }} />{['<30秒', '30–60', '60–120', '120–240', '≥240秒'][index]}</div>)}</div>}
      <p className="legend-note"><span className="swatch swatch-missing" />該整點無值或衝突；缺值非 0。</p>
      <p className="layer-scope-note">歷史推估、站間示意線；週六、週日共用週末模型。小時在左側面板切換，日型由「偏好」調整。</p>
    </>}
    <details className="snapshot-details"><summary>來源時間與限制</summary>
      {active.map((layer) => {
        const record = data?.supplemental_manifest?.records.find((record) => record.id === layer.key);
        return <div className="snapshot-record" key={layer.key}><b>{layer.label}</b><p>{record ? snapshotPeriodText(record) : '來源目錄載入中…'}</p>{record?.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}</div>;
      })}
      <p>完整來源連結見右上角「資料與方法」。</p>
    </details>
  </section>;
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

function TimeControls({ state, playing, onChange, onTogglePlay }: Pick<Props, 'state' | 'playing' | 'onChange' | 'onTogglePlay'>) {
  return <>
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
  </>;
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

export default function Panel({ state, playing, needCounts, data, supplementalPending, onRetrySupplemental, onChange, onTogglePlay, onPreferences }: Props) {
  const { labels } = scaleFor(state);
  const frequency = state.mode === 'frequency';
  return (
    <aside className="panel">
      <section className="card intro">
        <h1>城市裡的連結，<br />一眼看見。</h1>
        <p className="muted">雙北公車路網、里人口與交通快照。點選站點，查看所在里年齡與周邊設施。</p>

        {state.mode === 'coverage' && <Summary rows={coverageRows(state)} foot={`${state.coverageCell} m 網格`} onPreferences={onPreferences} />}
        {frequency && (
          <Summary rows={frequencyRows(state)} foot={`${state.cell} m 網格`} onPreferences={onPreferences}>
            <TimeControls state={state} playing={playing} onChange={onChange} onTogglePlay={onTogglePlay} />
          </Summary>
        )}
        {state.mode === 'need' && <Summary rows={needRows(state)} foot="250 m 網格" onPreferences={onPreferences} />}
        {!frequency && state.layers.has('bus_times') && <Summary rows={[{ key: 'bus_times', label: '站間推估旅時', value: DAY_LABELS[state.day], color: '#327caf' }]} foot="歷史推估，非即時旅時" onPreferences={onPreferences}>
          <TimeControls state={state} playing={playing} onChange={onChange} onTogglePlay={onTogglePlay} />
        </Summary>}
        {state.layers.has('villages') && <p className="layer-scope-note">里人口圖層已暫藏分析網格；可在「偏好」關閉以恢復。</p>}
        <ExtraStatus data={data} supplementalPending={supplementalPending} onRetrySupplemental={onRetrySupplemental} />
      </section>

      <ExtraLegend state={state} data={data} />

      {!state.layers.has('villages') && (frequency ? (
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
      ) : state.mode === 'need' ? <NeedLegend state={state} counts={needCounts} /> : <CoverageLegend state={state} />)}
    </aside>
  );
}
