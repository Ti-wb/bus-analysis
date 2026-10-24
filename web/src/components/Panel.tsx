import type { CSSProperties } from 'react';
import { ALL_DAY, CELL_SIZES, COLORS, COVERAGE_ALPHA, COVERAGE_LAYERS, DAY_LABELS, PLACE_CATEGORIES, STATION_TYPES } from '../config';
import { distanceText } from '../coverage';
import { hourText, scaleFor } from '../data';
import type { AnalysisMode, AppState, CoverageLayer, DayType, LayerGroup } from '../types';

interface Props {
  state: AppState;
  playing: boolean;
  onChange: (patch: Partial<AppState>) => void;
  onTogglePlay: () => void;
}

const MODES: { key: AnalysisMode; label: string }[] = [
  { key: 'frequency', label: '公車班次網格' },
  { key: 'coverage', label: '站點覆蓋' },
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

export default function Panel({ state, playing, onChange, onTogglePlay }: Props) {
  const { labels } = scaleFor(state);
  const frequency = state.mode === 'frequency';
  return (
    <aside className="panel">
      <section className="card intro">
        <h1>城市裡的連結，<br />一眼看見。</h1>
        <p className="muted">公車、捷運與 YouBike 的路網、班次與站點覆蓋</p>

        <h2>分析圖層</h2>
        <div className="segmented segmented-2" role="radiogroup" aria-label="分析圖層">
          {MODES.map((m) => (
            <button key={m.key} role="radio" aria-checked={state.mode === m.key} className={state.mode === m.key ? 'is-active' : ''} onClick={() => onChange({ mode: m.key })}>
              {m.label}
            </button>
          ))}
        </div>

        {!frequency && <CoverageControls state={state} onChange={onChange} />}

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
      ) : <CoverageLegend state={state} />}
    </aside>
  );
}
