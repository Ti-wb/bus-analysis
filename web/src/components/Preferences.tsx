import { useEffect, useState, type CSSProperties } from 'react';
import { COVERAGE_CELLS, COVERAGE_LAYERS, DEFAULT_COVERAGE_CELL, STATION_TYPES } from '../config';
import type { AppState, CoverageLayer, LayerGroup, StationType } from '../types';

/** Settings edited here; nothing changes on the map until they are applied. */
export type PreferencePatch = Pick<AppState, 'coverageCell' | 'walkTypes' | 'walkRadii' | 'rideRadius' | 'coverage' | 'gridLines' | 'labels' | 'layers'>;

interface Props {
  state: AppState;
  onApply: (patch: PreferencePatch) => void;
  onClose: () => void;
}

const MARKER_GROUPS: LayerGroup[] = STATION_TYPES.map((t) => t.markers);
const WALK = COVERAGE_LAYERS.walk;
const RIDE = COVERAGE_LAYERS.ride;

function pick(state: AppState): PreferencePatch {
  const { coverageCell, walkTypes, walkRadii, rideRadius, coverage, gridLines, labels, layers } = state;
  return { coverageCell, walkTypes, walkRadii, rideRadius, coverage, gridLines, labels, layers };
}

function defaults(state: AppState): PreferencePatch {
  const layers = new Set(state.layers);
  for (const group of MARKER_GROUPS) layers.add(group);
  return {
    coverageCell: DEFAULT_COVERAGE_CELL,
    walkTypes: new Set(STATION_TYPES.map((t) => t.key)),
    walkRadii: { bus: WALK.radius, mrt: WALK.radius, youbike: WALK.radius },
    rideRadius: RIDE.radius,
    coverage: new Set<CoverageLayer>(['walk', 'ride']),
    gridLines: true,
    labels: true,
    layers,
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
}

function RadiusControl({ label, layer, value, onChange, enabled = true, onEnabled }: RadiusProps) {
  const { min, max, step } = COVERAGE_LAYERS[layer];
  // The number field keeps what is typed and settles to the nearest valid value on blur.
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const n = Math.round(Number(text));
    const next = Number.isFinite(n) && text.trim() ? Math.min(max, Math.max(min, n)) : value;
    setText(String(next));
    if (next !== value) onChange(next);
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
            onChange={(e) => setText(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === 'Enter' && commit()}
          />
          <span>m</span>
        </label>
      </div>
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

export default function Preferences({ state, onApply, onClose }: Props) {
  const [draft, setDraft] = useState(() => pick(state));
  const set = (patch: Partial<PreferencePatch>) => setDraft((d) => ({ ...d, ...patch }));
  const setWalk = (type: StationType, radius: number) => set({ walkRadii: { ...draft.walkRadii, [type]: radius } });

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
              網格細緻程度
              <button className="link-btn" onClick={() => setDraft(defaults(state))}>重設預設</button>
            </h3>
            <div className="prefs-cells" role="radiogroup" aria-label="網格細緻程度">
              {COVERAGE_CELLS.map(({ cell, label }) => (
                <button key={cell} role="radio" aria-checked={draft.coverageCell === cell} className={draft.coverageCell === cell ? 'is-active' : ''} onClick={() => set({ coverageCell: cell })}>
                  {cell} m｜{label}
                </button>
              ))}
            </div>
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
                key={t.key}
                label={t.walkLabel}
                layer="walk"
                value={draft.walkRadii[t.key]}
                onChange={(r) => setWalk(t.key, r)}
                enabled={draft.walkTypes.has(t.key)}
                onEnabled={() => set({ walkTypes: toggled(draft.walkTypes, t.key) })}
              />
            ))}
            <RadiusControl label="YouBike・騎乘延伸" layer="ride" value={draft.rideRadius} onChange={(r) => set({ rideRadius: r })} />
            <p className="prefs-note">橙色表示步行範圍，青綠表示騎乘範圍；取消勾選的站點類型不計入步行範圍。</p>
          </section>

          <section>
            <h3>圖層顯示</h3>
            <Switch label="步行覆蓋" color={rgb('walk')} checked={draft.coverage.has('walk')} onChange={() => set({ coverage: toggled(draft.coverage, 'walk') })} />
            <Switch label="YouBike 騎乘覆蓋" color={rgb('ride')} checked={draft.coverage.has('ride')} onChange={() => set({ coverage: toggled(draft.coverage, 'ride') })} />
            <Switch label="網格線" color="#658c90" checked={draft.gridLines} onChange={() => set({ gridLines: !draft.gridLines })} />
            <p className="prefs-subhead">站點標記</p>
            <div className="prefs-markers">
              {STATION_TYPES.map((t) => (
                <label key={t.key}>
                  <input type="checkbox" checked={draft.layers.has(t.markers)} onChange={() => set({ layers: toggled(draft.layers, t.markers) })} />
                  {t.label}
                </label>
              ))}
            </div>
            <Switch label="地名與道路標籤" color="#658c90" checked={draft.labels} onChange={() => set({ labels: !draft.labels })} />
          </section>
        </div>

        <footer className="prefs-foot">
          <button className="prefs-cancel" onClick={onClose}>取消</button>
          <button className="prefs-apply" onClick={() => onApply(draft)}>套用設定</button>
        </footer>
      </aside>
    </div>
  );
}
