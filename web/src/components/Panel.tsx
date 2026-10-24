import type { CSSProperties } from 'react';
import { ALL_DAY, CELL_SIZES, COLORS, COVERAGE_ALPHA, COVERAGE_LAYERS, DAY_LABELS, DEFAULT_NEED_WEIGHTS, ELDERLY_COLORS, GAP_COLORS, NEED_BANDS, NEED_COLORS, NEED_WEIGHT_LABELS, PLACE_CATEGORIES, QUADRANTS, STATION_TYPES, VILLAGE_COLORS, type Quadrant } from '../config';
import { distanceText } from '../coverage';
import { hourText, scaleFor, type SupplementalKey } from '../data';
import { isDefaultWeights } from '../need';
import { snapshotPeriodText } from '../regional';
import type { AnalysisMode, AppState, CoverageLayer, DayType, LayerGroup, MapData, NeedView, NeedWeights } from '../types';

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
  { group: 'routes', label: '臺北公車路線', swatch: 'swatch-bus' },
  { group: 'stops', label: '臺北公車站牌', swatch: 'swatch-stop', note: () => '放大後顯示' },
  { group: 'mrt', label: '捷運路網', swatch: 'swatch-mrt' },
  { group: 'places', label: '重要地點', swatch: 'swatch-place', note: () => '點選類別篩選' },
  { group: 'youbike', label: 'YouBike 站點', swatch: 'swatch-youbike', note: () => '放大後顯示' },
];

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

function ExtraControls({ state, data, supplementalPending, onChange, onRetrySupplemental }: Pick<Props, 'state' | 'data' | 'supplementalPending' | 'onChange' | 'onRetrySupplemental'>) {
  return <>
    <h2>跨市資料圖層</h2>
    <p className="layer-scope-note">資料快照；班次、覆蓋與需求模型範圍仍為臺北市。</p>
    {EXTRA_LAYERS.map(({ group, key, label, swatch }) => {
      const loaded = data?.[key];
      const failed = data?.supplemental_errors.some((message) => message.startsWith(key));
      return <label className="layer-toggle extra-layer-toggle" key={group}>
        <input type="checkbox" checked={state.layers.has(group)} onChange={() => onChange({ layers: toggled(state.layers, group) })} />
        <span className={`swatch ${swatch}`} />{label}
        <small>{supplementalPending.has(key) ? '載入中…' : failed ? '載入失敗' : loaded && 'features' in loaded ? `${loaded.features.length.toLocaleString()} 筆` : '勾選載入'}</small>
      </label>;
    })}
    {(state.layers.has('regional_routes') || state.layers.has('regional_stops')) && <label className="sub-layer-toggle">
      <input type="checkbox" checked={state.leapfrogOnly} onChange={(event) => onChange({ leapfrogOnly: event.target.checked })} />僅顯示官方類別核對的跳蛙公車
    </label>}
    {state.layers.has('villages') && <>
      <div className="segmented segmented-2" role="radiogroup" aria-label="里人口著色方式">
        {([{ key: 'population', label: '戶籍人口' }, { key: 'elderly_share', label: '65 歲以上占比' }] as const).map((metric) => <button key={metric.key} role="radio" aria-checked={state.villageMetric === metric.key} className={state.villageMetric === metric.key ? 'is-active' : ''} onClick={() => onChange({ villageMetric: metric.key })}>{metric.label}</button>)}
      </div>
      <p className="layer-scope-note">顯示整里統計；暫藏分析網格，關閉此層即恢復。</p>
    </>}
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
      <p className="layer-scope-note">歷史推估、站間示意線；週六、週日共用週末模型。依下方時段切換。</p>
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

/** Settings now live in the preferences sidebar; this card only summarises them. */
function CoverageSummary({ state, onPreferences }: { state: AppState; onPreferences: () => void }) {
  const rows: { layer: CoverageLayer; value: string }[] = [
    { layer: 'walk', value: walkText(state) },
    { layer: 'ride', value: distanceText(state.rideRadius) },
  ];
  return (
    <div className="coverage-summary">
      <h2>目前顯示</h2>
      {rows.map(({ layer, value }) => (
        <div key={layer} className={`summary-row${state.coverage.has(layer) ? '' : ' is-off'}`} style={{ '--c': rgba(layer) } as CSSProperties}>
          <i />{COVERAGE_LAYERS[layer].label}
          <b>{state.coverage.has(layer) ? value : '關閉'}</b>
        </div>
      ))}
      <div className="summary-foot">
        <span>{state.coverageCell} m 網格</span>
        <button className="link-btn" onClick={onPreferences}>到「偏好」調整</button>
      </div>
    </div>
  );
}

function CoverageLegend({ state }: { state: AppState }) {
  const layers = (Object.keys(COVERAGE_LAYERS) as CoverageLayer[]).filter((layer) => state.coverage.has(layer));
  return (
    <section className="card legend">
      <h3>由站心向外，逐格變淡</h3>
      {layers.map((layer) => (
        <div key={layer} className="coverage-legend">
          <p>{COVERAGE_LAYERS[layer].label} · {layer === 'walk' ? walkText(state) : distanceText(state.rideRadius)}</p>
          <div className="legend-scale legend-scale-5">
            {COVERAGE_ALPHA.map((a) => <span key={a} style={{ background: rgba(layer, a) }} />)}
          </div>
          <div className="legend-ends"><span>站心</span><span>外圍</span></div>
        </div>
      ))}
      {!layers.length && <p className="muted">兩個覆蓋圖層都已關閉。</p>}
      <p className="legend-note">每格約 {state.coverageCell} m；直線半徑，未計路網與河川阻隔。</p>
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

export default function Panel({ state, playing, needCounts, data, supplementalPending, onRetrySupplemental, onChange, onTogglePlay, onPreferences }: Props) {
  const { labels } = scaleFor(state);
  const frequency = state.mode === 'frequency';
  return (
    <aside className="panel">
      <section className="card intro">
        <h1>城市裡的連結，<br />一眼看見。</h1>
        <p className="muted">雙北公車路網、里人口與交通快照。點選站點，查看所在里年齡與周邊設施。</p>

        <h2>分析圖層</h2>
        <div className="segmented" role="radiogroup" aria-label="分析圖層">
          {MODES.map((m) => (
            <button key={m.key} role="radio" aria-checked={state.mode === m.key} className={state.mode === m.key ? 'is-active' : ''} onClick={() => onChange({ mode: m.key })}>
              {m.label}
            </button>
          ))}
        </div>

        {state.mode === 'coverage' && <CoverageSummary state={state} onPreferences={onPreferences} />}
        {state.mode === 'need' && <NeedControls state={state} onChange={onChange} />}

        <ExtraControls state={state} data={data} supplementalPending={supplementalPending} onChange={onChange} onRetrySupplemental={onRetrySupplemental} />

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

        {(frequency || state.layers.has('bus_times')) && (
          <>
            {frequency && <><h2>網格大小</h2>
            <div className="segmented segmented-4" role="radiogroup" aria-label="網格大小">
              {CELL_SIZES.map((cell) => (
                <button key={cell} role="radio" aria-checked={state.cell === cell} className={state.cell === cell ? 'is-active' : ''} onClick={() => onChange({ cell })}>
                  {cell} m
                </button>
              ))}
            </div></>}

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
