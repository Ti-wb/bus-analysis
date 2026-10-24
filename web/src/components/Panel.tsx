import type { CSSProperties } from 'react';
import { ALL_DAY, CELL_SIZES, COLORS, DAY_LABELS, PLACE_CATEGORIES } from '../config';
import { hourText, scaleFor } from '../data';
import type { AppState, DayType, LayerGroup } from '../types';

interface Props {
  state: AppState;
  playing: boolean;
  onChange: (patch: Partial<AppState>) => void;
  onTogglePlay: () => void;
}

const LAYER_TOGGLES: { group: LayerGroup; label: string; swatch: string; note?: (s: AppState) => string }[] = [
  { group: 'grid', label: '公車班次網格', swatch: 'swatch-grid', note: (s) => `${s.cell} m` },
  { group: 'routes', label: '公車路線', swatch: 'swatch-bus' },
  { group: 'stops', label: '公車站牌', swatch: 'swatch-stop', note: () => '放大後顯示' },
  { group: 'mrt', label: '捷運路網', swatch: 'swatch-mrt' },
  { group: 'places', label: '重要地點', swatch: 'swatch-place', note: () => '點選類別篩選' },
  { group: 'youbike', label: 'YouBike 站點', swatch: 'swatch-youbike', note: () => '放大後顯示' },
];

function toggled<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export default function Panel({ state, playing, onChange, onTogglePlay }: Props) {
  const { labels } = scaleFor(state);
  return (
    <aside className="panel">
      <section className="card intro">
        <h1>城市裡的連結，<br />一眼看見。</h1>
        <p className="muted">公車與捷運路網，以及公車每小時班次</p>

        <h2>顯示圖層</h2>
        {LAYER_TOGGLES.map(({ group, label, swatch, note }) => (
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

        <h2>網格大小</h2>
        <div className="segmented segmented-2" role="radiogroup" aria-label="網格大小">
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
      </section>

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
    </aside>
  );
}
