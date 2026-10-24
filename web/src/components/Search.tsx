import { useMemo, useState } from 'react';
import { PLACE_BY_CATEGORY } from '../config';
import type { MapData } from '../types';

export interface SearchItem {
  name: string;
  kind: string;
  at: [number, number];
  zoom: number;
  /** Layer to switch on when this item is chosen. */
  layer?: 'stops' | 'places' | 'youbike';
  place?: string;
}

function buildIndex(data: MapData): SearchItem[] {
  return [
    ...data.district_labels.features.map((f) => ({ name: `${f.properties.name}區`, kind: '行政區', at: f.geometry.coordinates as [number, number], zoom: 13 })),
    ...data.mrt_stations.features.map((f) => ({ name: f.properties.name, kind: '軌道車站', at: f.geometry.coordinates as [number, number], zoom: 15.5 })),
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
    })),
    ...data.youbike_stations.features.filter((f) => f.properties.name).map((f) => ({
      name: f.properties.name!,
      kind: 'YouBike',
      at: f.geometry.coordinates as [number, number],
      zoom: 16.5,
      layer: 'youbike' as const,
    })),
  ];
}

export default function Search({ data, onChoose }: { data: MapData; onChoose: (item: SearchItem) => void }) {
  const index = useMemo(() => buildIndex(data), [data]);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const matches = useMemo(() => {
    const q = query.trim();
    return q ? index.filter((item) => item.name.includes(q)).slice(0, 12) : [];
  }, [index, query]);

  const choose = (item: SearchItem | undefined) => {
    if (!item) return;
    setQuery(item.name);
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
        type="search"
        placeholder="搜尋行政區、捷運站、重要地點或公車站牌"
        autoComplete="off"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') choose(matches[0]);
          if (e.key === 'Escape') setOpen(false);
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && matches.length > 0 && (
        <ul className="search-results">
          {matches.map((m, i) => (
            <li key={`${m.kind}-${m.name}-${i}`} onMouseDown={() => choose(m)}>
              {m.name}<small>{m.kind}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
