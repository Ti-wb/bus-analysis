import { useCallback, useEffect, useId, useMemo, useRef, useState, type Ref } from 'react';
import { PLACE_BY_CATEGORY } from '../config';
import type { MapData, StationSelection } from '../types';

export interface SearchItem {
  name: string;
  kind: string;
  at: [number, number];
  zoom: number;
  /** Layer to switch on when this item is chosen. */
  layer?: 'stops' | 'mrt' | 'places' | 'youbike' | 'regional_stops';
  place?: string;
  station?: StationSelection;
}

function buildIndex(data: MapData): SearchItem[] {
  return [
    ...data.district_labels.features.map((f) => ({ name: `${f.properties.name}區`, kind: '行政區', at: f.geometry.coordinates as [number, number], zoom: 13 })),
    ...data.mrt_stations.features.map((f) => ({ name: f.properties.name, kind: '捷運站', at: f.geometry.coordinates as [number, number], zoom: 15.5, layer: 'mrt' as const,
      station: { type: 'mrt' as const, id: f.properties.id, at: f.geometry.coordinates as [number, number], properties: f.properties } })),
    ...data.places.features.filter((f) => f.properties.name).map((f) => ({
      name: f.properties.name!,
      kind: PLACE_BY_CATEGORY[f.properties.category]?.label ?? '地點',
      at: f.geometry.coordinates as [number, number],
      zoom: 16,
      layer: 'places' as const,
      place: PLACE_BY_CATEGORY[f.properties.category]?.key,
    })),
    ...data.bus_stops.features.map((f) => ({
      name: f.properties.name,
      kind: `站牌 · ${f.properties.route_count} 線`,
      at: f.geometry.coordinates as [number, number],
      zoom: 16.5,
      layer: 'stops' as const,
      station: { type: 'bus' as const, id: f.properties.id, at: f.geometry.coordinates as [number, number], properties: f.properties },
    })),
    ...data.regional_bus_stops?.features.map((f) => ({
      name: f.properties.name,
      kind: `新北站位 · ${f.properties.route_count} 線`,
      at: f.geometry.coordinates as [number, number], zoom: 16.5, layer: 'regional_stops' as const,
      station: { type: 'bus' as const, id: f.properties.id, at: f.geometry.coordinates as [number, number], properties: f.properties },
    })) ?? [],
    ...data.youbike_stations.features.filter((f) => f.properties.name).map((f) => ({
      name: f.properties.name!,
      kind: 'YouBike',
      at: f.geometry.coordinates as [number, number],
      zoom: 16.5,
      layer: 'youbike' as const,
      station: { type: 'youbike' as const, id: f.properties.station_id, at: f.geometry.coordinates as [number, number], properties: f.properties },
    })),
  ];
}

export default function Search({ data, onChoose, inputRef }: { data: MapData; onChoose: (item: SearchItem) => void; inputRef?: Ref<HTMLInputElement> }) {
  const field = useRef<HTMLInputElement>(null);
  const results = useRef<HTMLUListElement>(null);
  const resultPointer = useRef<string | null>(null);
  const resultsId = useId();
  const connectInput = useCallback((node: HTMLInputElement | null) => {
    field.current = node;
    if (typeof inputRef === 'function') return inputRef(node);
    if (inputRef) inputRef.current = node;
  }, [inputRef]);
  const index = useMemo(() => buildIndex(data), [data]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => {
    const q = query.trim();
    const found: SearchItem[] = [];
    if (q) for (const item of index) {
      if (item.name.includes(q)) found.push(item);
      if (found.length === 12) break;
    }
    return found;
  }, [index, query]);
  const visible = open && query.trim().length > 0;

  useEffect(() => {
    const list = results.current;
    const option = list?.children[active] as HTMLElement | undefined;
    if (!visible || !list || !option) return;
    // Scroll only the result list, leaving the map and surrounding panels in place.
    if (option.offsetTop < list.scrollTop) list.scrollTop = option.offsetTop;
    else if (option.offsetTop + option.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = option.offsetTop + option.offsetHeight - list.clientHeight;
    }
  }, [active, visible, matches]);

  const choose = (item: SearchItem | undefined, touch = false) => {
    resultPointer.current = null;
    if (!item) return;
    setQuery(item.name);
    // A station name can also match a district or another station type. Keep the
    // chosen result selected when Escape returns focus to this search field.
    setActive(Math.max(0, index.filter((candidate) => candidate.name.includes(item.name)).slice(0, 12).indexOf(item)));
    setOpen(false);
    onChoose(item);
    if (touch) field.current?.blur();
  };

  return (
    <div className="search" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2" />
      </svg>
      <input
        ref={connectInput}
        type="search"
        placeholder="搜尋捷運、公車、YouBike 或地點"
        aria-label="搜尋站點與地點"
        role="combobox"
        aria-expanded={visible && matches.length > 0}
        aria-controls={visible && matches.length > 0 ? resultsId : undefined}
        aria-autocomplete="list"
        aria-activedescendant={visible && matches[active] ? `${resultsId}-${active}` : undefined}
        autoComplete="off"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault(); setOpen(true);
            setActive((i) => Math.max(0, Math.min(matches.length - 1, i + (open ? e.key === 'ArrowDown' ? 1 : -1 : 0))));
          }
          if (e.key === 'Enter') { e.preventDefault(); choose(matches[active]); }
          if (e.key === 'Escape' && visible) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
        }}
      />
      {query && <button className="search-clear" aria-label="清除站點搜尋" onClick={() => { setQuery(''); setActive(0); field.current?.focus({ preventScroll: true }); }}>
        <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
      </button>}
      {visible && matches.length > 0 && (
        <ul ref={results} className="search-results" id={resultsId} role="listbox" aria-label="搜尋結果">
          {matches.map((m, i) => (
            <li key={`${m.kind}-${m.name}-${i}`} id={`${resultsId}-${i}`} role="option" aria-selected={i === active} className={i === active ? 'is-active' : ''}
              onPointerDown={(e) => { resultPointer.current = e.pointerType; }} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(m, resultPointer.current === 'touch')}>
              {m.name}<small>{m.kind}</small>
            </li>
          ))}
        </ul>
      )}
      {visible && !matches.length && <div className="search-empty" role="status"><b>找不到相符的站點或地點</b><p>試試較短的名稱，或移動地圖尋找站點。</p></div>}
    </div>
  );
}
