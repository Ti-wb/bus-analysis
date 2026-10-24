import { useEffect, useId, useState, type CSSProperties } from 'react';
import { CELL_SIZES, COLORS, COVERAGE_CELLS, COVERAGE_LAYERS, DAY_LABELS, DEFAULT_COVERAGE_CELL, DEFAULT_LAYERS, DEFAULT_NEED_WEIGHTS, NEED_BANDS, NEED_WEIGHT_LABELS, PLACE_CATEGORIES, STATION_TYPES } from '../config';
import { isDefaultWeights } from '../need';
import type { SupplementalKey } from '../data';
import type { AnalysisMode, AppState, CoverageLayer, DayType, LayerGroup, MapData, NeedView, NeedWeights, StationType } from '../types';
import { ExtraControls } from './Panel';

/** Settings edited here; nothing changes on the map until they are applied. */
export type PreferencePatch = Pick<AppState,
  | 'mode' | 'cell' | 'day' | 'band' | 'needView' | 'weights'
  | 'coverageCell' | 'walkTypes' | 'walkRadii' | 'rideRadius' | 'coverage' | 'gridLines'
  | 'labels' | 'layers' | 'places' | 'leapfrogOnly' | 'villageMetric'>;

interface Props {
  state: AppState;
  data: MapData | null;
  supplementalPending: Set<SupplementalKey>;
  onRetrySupplemental: () => void;
  onApply: (patch: PreferencePatch) => void;
  onClose: () => void;
}

export const MODES: { key: AnalysisMode; label: string; note: string }[] = [
  { key: 'coverage', label: '站點覆蓋', note: '步行到站與 YouBike 騎乘的直線範圍' },
  { key: 'frequency', label: '班次網格', note: '每格公車計畫發車數，可依小時播放' },
  { key: 'need', label: '需求落差', note: '同時段需求與供給排名的比較' },
];

const NEED_VIEWS: { key: NeedView; label: string }[] = [
  { key: 'quadrant', label: '需求 × 供給' },
  { key: 'gap', label: '落差值' },
];

const RADIUS_FIELDS = [...STATION_TYPES.map((t) => t.key), 'ride'];
const WALK = COVERAGE_LAYERS.walk;
const RIDE = COVERAGE_LAYERS.ride;
const NEUTRAL = '#658c90';

function pick(state: AppState): PreferencePatch {
  const { mode, cell, day, band, needView, weights, coverageCell, walkTypes, walkRadii, rideRadius, coverage, gridLines, labels, layers, places, leapfrogOnly, villageMetric } = state;
  return { mode, cell, day, band, needView, weights, coverageCell, walkTypes, walkRadii, rideRadius, coverage, gridLines, labels, layers, places, leapfrogOnly, villageMetric };
}

/** Defaults for every setting except the analysis mode being edited. */
function defaults(mode: AnalysisMode): PreferencePatch {
  return {
    mode,
    cell: 500,
    day: 'weekday',
    band: 'weekday_am',
    needView: 'quadrant',
    weights: DEFAULT_NEED_WEIGHTS,
    coverageCell: DEFAULT_COVERAGE_CELL,
    walkTypes: new Set(STATION_TYPES.map((t) => t.key)),
    walkRadii: { bus: WALK.radius, mrt: WALK.radius, youbike: WALK.radius },
    rideRadius: RIDE.radius,
    coverage: new Set<CoverageLayer>(['walk', 'ride']),
    gridLines: true,
    labels: true,
    layers: new Set(DEFAULT_LAYERS),
    places: new Set(PLACE_CATEGORIES.filter((c) => c.on).map((c) => c.key)),
    leapfrogOnly: false,
    villageMetric: 'population',
  };
}

function toggled<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

const rgb = (layer: CoverageLayer) => `rgb(${COVERAGE_LAYERS[layer].rgb.join(',')})`;

interface RadiusProps {
  label: string;
  layer: CoverageLayer;
  value: number;
  onChange: (value: number) => void;
  /** Whether this station type counts towards walking reach; absent for the riding radius. */
  enabled?: boolean;
  onEnabled?: () => void;
  /** Reports whether the typed value can be applied. */
  onValidity: (valid: boolean) => void;
}

function RadiusControl({ label, layer, value, onChange, enabled = true, onEnabled, onValidity }: RadiusProps) {
  const { min, max, step } = COVERAGE_LAYERS[layer];
  const hintId = useId();
  // The number field keeps what is typed; only in-range whole metres reach the draft.
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const inRange = (n: number) => Number.isInteger(n) && n >= min && n <= max;
  const invalid = enabled && !(text.trim() && inRange(Number(text)));
  useEffect(() => onValidity(!invalid), [invalid, onValidity]);
  // A field that leaves the drawer (another mode picked) no longer blocks applying.
  useEffect(() => () => onValidity(true), [onValidity]);
  const type = (next: string) => {
    setText(next);
    const n = Number(next);
    if (next.trim() && inRange(n) && n !== value) onChange(n);
  };
  const fill = ((value - min) / (max - min)) * 100;
  return (
    <div className={`pref-radius${enabled ? '' : ' is-off'}`} style={{ '--c': rgb(layer), '--fill': `${fill}%` } as CSSProperties}>
      <div className="pref-radius-head">
        {onEnabled
          ? <label className="pref-type"><input type="checkbox" checked={enabled} onChange={onEnabled} />{label}</label>
          : <span className="pref-type"><i />{label}</span>}
        <label className="pref-number">
          <input
            type="number"
            min={min}
            max={max}
            step={step}
            value={text}
            disabled={!enabled}
            aria-label={`${label}半徑（公尺）`}
            aria-invalid={invalid}
            aria-describedby={invalid ? hintId : undefined}
            onChange={(e) => type(e.target.value)}
          />
          <span>m</span>
        </label>
      </div>
      {invalid && <p className="pref-error" id={hintId}>請輸入 {min}–{max} m 的整數</p>}
      <input type="range" min={min} max={max} step={step} value={value} disabled={!enabled} aria-label={`${label}半徑`} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function Switch({ label, color, checked, onChange }: { label: string; color: string; checked: boolean; onChange: () => void }) {
  return (
    <label className="pref-switch" style={{ '--c': color } as CSSProperties}>
      <i />{label}
      <input type="checkbox" role="switch" checked={checked} onChange={onChange} />
    </label>
  );
}

interface ChoiceProps<T extends string | number> {
  label: string;
  options: { key: T; label: string; title?: string }[];
  value: T;
  onChange: (value: T) => void;
  columns?: number;
}

function Choice<T extends string | number>({ label, options, value, onChange, columns = options.length }: ChoiceProps<T>) {
  return (
    <div className="prefs-cells" role="radiogroup" aria-label={label} style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}>
      {options.map((o) => (
        <button key={o.key} role="radio" aria-checked={value === o.key} title={o.title} className={value === o.key ? 'is-active' : ''} onClick={() => onChange(o.key)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function Preferences({ state, data, supplementalPending, onRetrySupplemental, onApply, onClose }: Props) {
  const [draft, setDraft] = useState(() => pick(state));
  const set = (patch: Partial<PreferencePatch>) => setDraft((d) => ({ ...d, ...patch }));
  const setWalk = (type: StationType, radius: number) => set({ walkRadii: { ...draft.walkRadii, [type]: radius } });
  const setWeight = (k: number, value: number) => {
    const weights = [...draft.weights] as NeedWeights;
    weights[k] = value;
    if (weights.some((w) => w > 0)) set({ weights });
  };
  const toggleLayer = (group: LayerGroup) => set({ layers: toggled(draft.layers, group) });
  // Fields holding an out-of-range value; the draft keeps its last valid value and cannot be applied.
  const [invalid, setInvalid] = useState<ReadonlySet<string>>(new Set());
  const [validityOf] = useState(() => Object.fromEntries(RADIUS_FIELDS.map((field) => [field, (valid: boolean) => setInvalid((prev) => {
    if (valid !== prev.has(field)) return prev;
    const next = new Set(prev);
    if (valid) next.delete(field);
    else next.add(field);
    return next;
  })])));
  // Remounting the fields on reset drops any out-of-range text they hold.
  const [resets, setResets] = useState(0);
  const weightTotal = draft.weights.reduce((a, b) => a + b, 0) || 1;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="prefs-backdrop" onClick={onClose}>
      <aside className="prefs" role="dialog" aria-modal="true" aria-labelledby="prefs-title" onClick={(e) => e.stopPropagation()}>
        <header className="prefs-head">
          <div>
            <h2 id="prefs-title">偏好設定</h2>
            <p>調整地圖顯示與站點覆蓋範圍</p>
          </div>
          <button className="prefs-close" aria-label="關閉偏好設定" onClick={onClose}>✕</button>
        </header>

        <div className="prefs-body">
          <section>
            <h3 className="prefs-section-head">
              分析圖層
              <button className="link-btn" onClick={() => { setDraft(defaults(draft.mode)); setResets((n) => n + 1); }}>重設預設</button>
            </h3>
            <Choice label="分析圖層" options={MODES} value={draft.mode} onChange={(m) => set({ mode: m })} />
            <p className="prefs-note">{MODES.find((m) => m.key === draft.mode)!.note}</p>
          </section>

          <section>
            <ExtraControls state={{ ...state, ...draft }} data={data} supplementalPending={supplementalPending} onRetrySupplemental={onRetrySupplemental} onChange={set} />
          </section>

          {draft.mode === 'coverage' && (
            <>
              <section>
                <h3>網格細緻程度</h3>
                <Choice label="網格細緻程度" options={COVERAGE_CELLS.map(({ cell, label }) => ({ key: cell, label: `${cell} m｜${label}` }))} value={draft.coverageCell} onChange={(c) => set({ coverageCell: c })} />
                <p className="prefs-note">格子越小，擴散範圍的邊緣越細緻。</p>
                <div className="prefs-cell-preview">
                  <span className="pixel-sample" aria-hidden="true">{[...'otgottogtogg'].map((c, k) => <i key={k} className={`px-${c}`} />)}</span>
                  <div>
                    <b>{draft.coverageCell} × {draft.coverageCell} 公尺</b>
                    <small>全圖共用網格，未覆蓋格保持透明</small>
                  </div>
                </div>
              </section>

              <section>
                <h3>站點擴散範圍</h3>
                <p className="prefs-note">由站點向外擴散，以直線半徑計算</p>
                {STATION_TYPES.map((t) => (
                  <RadiusControl
                    key={`${t.key}-${resets}`}
                    label={t.walkLabel}
                    layer="walk"
                    value={draft.walkRadii[t.key]}
                    onChange={(r) => setWalk(t.key, r)}
                    enabled={draft.walkTypes.has(t.key)}
                    onEnabled={() => set({ walkTypes: toggled(draft.walkTypes, t.key) })}
                    onValidity={validityOf[t.key]}
                  />
                ))}
                <RadiusControl key={`ride-${resets}`} label="YouBike・騎乘延伸" layer="ride" value={draft.rideRadius} onChange={(r) => set({ rideRadius: r })} onValidity={validityOf.ride} />
                <p className="prefs-note">橙色表示步行範圍，青綠表示騎乘範圍；取消勾選的站點類型不計入步行範圍。</p>
              </section>
            </>
          )}

          {draft.mode === 'frequency' && (
            <section>
              <h3>網格大小</h3>
              <Choice label="網格大小" options={CELL_SIZES.map((c) => ({ key: c, label: `${c} m` }))} value={draft.cell} onChange={(c) => set({ cell: c })} />
              <p className="prefs-note">每格加總格內公車站牌所屬附線的計畫發車數。</p>
              <h3>日型</h3>
              <Choice label="日型" options={(Object.keys(DAY_LABELS) as DayType[]).map((d) => ({ key: d, label: DAY_LABELS[d] }))} value={draft.day} onChange={(d) => set({ day: d })} />
              <p className="prefs-note">小時在左側面板切換或播放。</p>
            </section>
          )}

          {draft.mode !== 'frequency' && draft.layers.has('bus_times') && <section>
            <h3>公車推估旅時日型</h3>
            <Choice label="旅時日型" options={(Object.keys(DAY_LABELS) as DayType[]).map((day) => ({ key: day, label: DAY_LABELS[day] }))} value={draft.day} onChange={(day) => set({ day })} />
            <p className="prefs-note">小時在左側面板切換或播放；週六與週日共用來源週末模型。</p>
          </section>}

          {draft.mode === 'need' && (
            <section>
              <h3>時段</h3>
              <Choice label="時段" options={NEED_BANDS.map((b) => ({ key: b.key, label: b.short, title: b.label }))} value={draft.band} onChange={(b) => set({ band: b })} columns={3} />
              <p className="prefs-note">{NEED_BANDS.find((b) => b.key === draft.band)!.label}</p>
              <h3>呈現方式</h3>
              <Choice label="呈現方式" options={NEED_VIEWS} value={draft.needView} onChange={(v) => set({ needView: v })} />
              <h3 className="prefs-section-head prefs-weights-head">
                需求權重
                {!isDefaultWeights(draft.weights) && <button className="link-btn" onClick={() => set({ weights: DEFAULT_NEED_WEIGHTS })}>重設權重</button>}
              </h3>
              {NEED_WEIGHT_LABELS.map((label, k) => (
                <label key={label} className="need-weight">
                  <span><i className={`need-part-${k}`} />{label}</span>
                  <input type="range" min={0} max={100} step={5} value={draft.weights[k]} aria-label={`${label}權重`} onChange={(e) => setWeight(k, Number(e.target.value))} />
                  <b>{Math.round((draft.weights[k] / weightTotal) * 100)}%</b>
                </label>
              ))}
              <p className="prefs-note">預設 50／30／20 是方法文件的假設值；總和自動換算成 100%。</p>
            </section>
          )}

          <section>
            <h3>圖層顯示</h3>
            {draft.mode === 'coverage' && (
              <>
                <Switch label="步行覆蓋" color={rgb('walk')} checked={draft.coverage.has('walk')} onChange={() => set({ coverage: toggled(draft.coverage, 'walk') })} />
                <Switch label="YouBike 騎乘覆蓋" color={rgb('ride')} checked={draft.coverage.has('ride')} onChange={() => set({ coverage: toggled(draft.coverage, 'ride') })} />
                <Switch label="網格線" color={NEUTRAL} checked={draft.gridLines} onChange={() => set({ gridLines: !draft.gridLines })} />
              </>
            )}
            {draft.mode === 'frequency' && (
              <Switch label="公車班次網格" color={COLORS[4]} checked={draft.layers.has('grid')} onChange={() => toggleLayer('grid')} />
            )}
            <Switch label="臺北公車路線" color="var(--bus)" checked={draft.layers.has('routes')} onChange={() => toggleLayer('routes')} />
            <Switch label="重要地點" color="#c2549d" checked={draft.layers.has('places')} onChange={() => toggleLayer('places')} />
            {draft.layers.has('places') && (
              <div className="place-chips prefs-place-chips">
                {PLACE_CATEGORIES.map((c) => (
                  <button
                    key={c.key}
                    className={`place-chip${draft.places.has(c.key) ? ' is-on' : ''}`}
                    style={{ '--c': c.color } as CSSProperties}
                    aria-pressed={draft.places.has(c.key)}
                    onClick={() => set({ places: toggled(draft.places, c.key) })}
                  >
                    <i />{c.label}
                  </button>
                ))}
              </div>
            )}
            <p className="prefs-subhead">站點標記<small>公車站牌與 YouBike 放大後顯示</small></p>
            <div className="prefs-markers">
              {STATION_TYPES.map((t) => (
                <label key={t.key}>
                  <input type="checkbox" checked={draft.layers.has(t.markers)} onChange={() => toggleLayer(t.markers)} />
                  {t.label}
                </label>
              ))}
            </div>
            <Switch label="地名與道路標籤" color={NEUTRAL} checked={draft.labels} onChange={() => set({ labels: !draft.labels })} />
          </section>
        </div>

        <footer className="prefs-foot">
          {invalid.size > 0 && <p className="prefs-foot-note" role="status">修正範圍外的數值後才能套用</p>}
          <button className="prefs-cancel" onClick={onClose}>取消</button>
          <button className="prefs-apply" disabled={invalid.size > 0} onClick={() => onApply(draft)}>套用設定</button>
        </footer>
      </aside>
    </div>
  );
}
