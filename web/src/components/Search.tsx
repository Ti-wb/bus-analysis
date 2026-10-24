import { useMemo, useState, type Ref } from 'react';
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
  const index = useMemo(() => buildIndex(data), [data]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => {
    const q = query.trim();
    return q ? index.filter((item) => item.name.includes(q)).slice(0, 12) : [];
  }, [index, query]);

  const choose = (item: SearchItem | undefined) => {
    if (!item) return;
    setQuery(item.name);
    // A station name can also match a district or another station type. Keep the
    // chosen result selected when Escape returns focus to this search field.
    setActive(Math.max(0, index.filter((candidate) => candidate.name.includes(item.name)).slice(0, 12).indexOf(item)));
    setOpen(false);
    onChoose(item);
  };

  return (
    <div className="search">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="2" />
      </svg>
      <input
        ref={inputRef}
        type="search"
        placeholder="搜尋捷運、公車、YouBike 或地點"
        aria-label="搜尋站點與地點"
        role="combobox"
        aria-expanded={open && matches.length > 0}
        aria-controls="station-search-results"
        aria-autocomplete="list"
        aria-activedescendant={open && matches[active] ? `station-search-${active}` : undefined}
        autoComplete="off"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault(); setOpen(true);
            setActive((i) => Math.max(0, Math.min(matches.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1))));
          }
          if (e.key === 'Enter') { e.preventDefault(); choose(matches[active]); }
          if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false); }
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && matches.length > 0 && (
        <ul className="search-results" id="station-search-results" role="listbox" aria-label="搜尋結果">
          {matches.map((m, i) => (
            <li key={`${m.kind}-${m.name}-${i}`} id={`station-search-${i}`} role="option" aria-selected={i === active} className={i === active ? 'is-active' : ''} onMouseDown={(e) => { e.preventDefault(); choose(m); }}>
              {m.name}<small>{m.kind}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
