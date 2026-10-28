import { useEffect, useMemo, useRef, useState } from 'react';
import { Marker, useMap } from 'react-map-gl/maplibre';
import { pathHandles } from './pathHandles';
import type { Coordinate } from './types';

export default function PathHandles({ path, onChange }: { path: Coordinate[]; onChange: (path: Coordinate[]) => void }) {
  const { current: map } = useMap();
  const [view, setView] = useState(0);
  const [focused, setFocused] = useState<number | null>(null);
  const focusRequested = useRef(false);
  useEffect(() => {
    if (!map) return;
    const update = () => setView((value) => value + 1);
    // Existing handles move with MapLibre; update the visible set only after a gesture.
    map.on('moveend', update); map.on('resize', update);
    return () => { map.off('moveend', update); map.off('resize', update); };
  }, [map]);
  const handles = useMemo(() => {
    if (!map) return { vertices: [], midpoints: [] };
    const container = map.getContainer();
    return pathHandles(path, (point) => map.project(point), container.clientWidth, container.clientHeight, focused);
  }, [path, map, view, focused]);
  // Marker attaches its portal in an effect; focus after that attachment.
  useEffect(() => {
    if (!focusRequested.current || focused === null || !map) return;
    focusRequested.current = false;
    const container = map.getContainer(), point = map.project(path[focused]);
    if (point.x < 24 || point.y < 24 || point.x > container.clientWidth - 24 || point.y > container.clientHeight - 24) map.panTo(path[focused], { duration: 0 });
    container.querySelector<HTMLButtonElement>(`[data-path-index="${focused}"]`)?.focus({ preventScroll: true });
  }, [focused, map, path]);

  return <>
    {handles.vertices.map((i) => {
      const at = path[i];
      return <Marker key={`vertex:${i}`} longitude={at[0]} latitude={at[1]} draggable
        onDragEnd={(e) => onChange(path.map((p, j) => j === i ? [e.lngLat.lng, e.lngLat.lat] : p))}>
        <button className="route-vertex" data-path-index={i} aria-label={`路徑控制點 ${i + 1}；Delete 移除`} title="拖曳調整；Tab 逐點查看，Insert 加點，Delete 移除"
          onFocus={() => setFocused(i)} onBlur={() => setFocused(null)}
          onClick={(e) => e.stopPropagation()} onKeyDown={(e) => {
            // Every original vertex remains reachable, including visually crowded ones.
            if (e.key === 'Tab') {
              const next = i + (e.shiftKey ? -1 : 1);
              if (next >= 0 && next < path.length) { e.preventDefault(); focusRequested.current = true; setFocused(next); }
            }
            if (e.key === 'Insert' && i < path.length - 1) {
              e.preventDefault(); onChange([...path.slice(0, i + 1), [(at[0] + path[i + 1][0]) / 2, (at[1] + path[i + 1][1]) / 2], ...path.slice(i + 1)]);
            }
            if ((e.key === 'Delete' || e.key === 'Backspace') && path.length > 2) { e.preventDefault(); onChange(path.filter((_, j) => i !== j)); }
            const delta = .0001;
            if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
              e.preventDefault(); e.stopPropagation();
              const next: Coordinate = [at[0] + (e.key === 'ArrowLeft' ? -delta : e.key === 'ArrowRight' ? delta : 0), at[1] + (e.key === 'ArrowDown' ? -delta : e.key === 'ArrowUp' ? delta : 0)];
              onChange(path.map((p, j) => j === i ? next : p));
            }
          }} />
      </Marker>;
    })}
    {handles.midpoints.map((i) => {
      const at: Coordinate = [(path[i][0] + path[i + 1][0]) / 2, (path[i][1] + path[i + 1][1]) / 2];
      return <Marker key={`mid:${i}`} longitude={at[0]} latitude={at[1]}>
        <button className="route-midpoint" title="新增路徑控制點" aria-label={`在路徑第 ${i + 1} 段新增控制點`} onClick={(e) => {
          e.stopPropagation(); onChange([...path.slice(0, i + 1), at, ...path.slice(i + 1)]);
        }}>+</button>
      </Marker>;
    })}
  </>;
}
