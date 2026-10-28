import { useEffect, useMemo, useRef, useState } from 'react';
import { Marker, useMap } from 'react-map-gl/maplibre';
import type { GeoJSONSource } from 'maplibre-gl';
import { pathHandles } from './pathHandles';
import type { Coordinate } from './types';

export default function PathHandles({ path, sourceId, onChange }: { path: Coordinate[]; sourceId: string; onChange: (path: Coordinate[]) => void }) {
  const { current: map } = useMap();
  const [view, setView] = useState(0);
  const [focused, setFocused] = useState<number | null>(null);
  const focusRequested = useRef(false);
  const drag = useRef<{ index: number; at: Coordinate } | null>(null);
  const dragFrame = useRef<number | null>(null);
  const focusVertex = (index: number) => { focusRequested.current = true; setFocused(index); };
  const movedPath = (index: number, at: Coordinate) => path.map((point, i) => i === index ? at : point);
  const showPath = (coordinates: Coordinate[]) => {
    if (!map?.getStyle()?.sources[sourceId]) return;
    void map.getSource<GeoJSONSource>(sourceId)?.setData({ type: 'FeatureCollection', features: [
      { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } },
    ] });
  };
  const cancelDragFrame = () => { if (dragFrame.current !== null) cancelAnimationFrame(dragFrame.current); dragFrame.current = null; };
  const previewDrag = (index: number, at: Coordinate) => {
    // Preview only the line; record one draft edit when the gesture ends.
    drag.current = { index, at };
    if (dragFrame.current !== null) return;
    dragFrame.current = requestAnimationFrame(() => {
      dragFrame.current = null;
      if (drag.current) showPath(movedPath(drag.current.index, drag.current.at));
    });
  };
  useEffect(() => () => {
    cancelDragFrame();
    if (drag.current) { drag.current = null; showPath(path); }
  }, [map, path, sourceId]);
  useEffect(() => {
    if (!map) return;
    const update = () => setView((value) => value + 1);
    const pointer = window.matchMedia('(pointer: coarse)');
    // Existing handles move with MapLibre; update the visible set only after a gesture.
    map.on('moveend', update); map.on('resize', update);
    pointer.addEventListener('change', update);
    return () => { map.off('moveend', update); map.off('resize', update); pointer.removeEventListener('change', update); };
  }, [map]);
  const handles = useMemo(() => {
    if (!map) return { vertices: [], midpoints: [] };
    const container = map.getContainer();
    return pathHandles(path, (point) => map.project(point), container.clientWidth, container.clientHeight, focused, window.matchMedia('(pointer: coarse)').matches ? 48 : 24);
  }, [path, map, view, focused]);
  // Marker effects attach (and in development reattach) their portal before the next frame.
  useEffect(() => {
    if (!focusRequested.current || focused === null || !map) return;
    const frame = requestAnimationFrame(() => {
      focusRequested.current = false;
      if (!path[focused]) return;
      const container = map.getContainer(), point = map.project(path[focused]);
      if (point.x < 24 || point.y < 24 || point.x > container.clientWidth - 24 || point.y > container.clientHeight - 24) map.panTo(path[focused], { duration: 0 });
      container.querySelector<HTMLButtonElement>(`[data-path-index="${focused}"]`)?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [focused, map, path]);

  return <>
    {handles.vertices.map((i) => {
      const at = path[i];
      return <Marker key={`vertex:${i}`} longitude={at[0]} latitude={at[1]} draggable
        onDragStart={() => map?.stop()}
        onDrag={(e) => previewDrag(i, [e.lngLat.lng, e.lngLat.lat])}
        onDragEnd={(e) => {
          cancelDragFrame(); drag.current = null;
          const next: Coordinate = [e.lngLat.lng, e.lngLat.lat];
          // Returning to the start can differ slightly because markers round to pixels.
          if (map && map.project(at).dist(map.project(next)) < 1) { e.target.setLngLat(at); showPath(path); return; }
          focusVertex(i); onChange(movedPath(i, next));
        }}>
        <button className="route-vertex" data-path-index={i} aria-label={`路徑控制點 ${i + 1}；Delete 移除`} title="拖曳調整；Tab 逐點查看，Insert 加點，Delete 移除，Esc 返回地圖"
          onFocus={() => setFocused(i)} onBlur={() => { if (!focusRequested.current) setFocused(null); }}
          onClick={(e) => e.stopPropagation()} onKeyDown={(e) => {
            // Every original vertex remains reachable, including visually crowded ones.
            if (e.key === 'Tab') {
              const next = i + (e.shiftKey ? -1 : 1);
              if (next >= 0 && next < path.length) { e.preventDefault(); focusVertex(next); }
            }
            if (e.key === 'Insert' && i < path.length - 1) {
              e.preventDefault(); focusVertex(i + 1); onChange([...path.slice(0, i + 1), [(at[0] + path[i + 1][0]) / 2, (at[1] + path[i + 1][1]) / 2], ...path.slice(i + 1)]);
            }
            if ((e.key === 'Delete' || e.key === 'Backspace') && path.length > 2) { e.preventDefault(); focusVertex(Math.min(i, path.length - 2)); onChange(path.filter((_, j) => i !== j)); }
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); focusRequested.current = false; setFocused(null); map?.getCanvas().focus({ preventScroll: true }); }
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
          e.stopPropagation(); focusVertex(i + 1); onChange([...path.slice(0, i + 1), at, ...path.slice(i + 1)]);
        }}>+</button>
      </Marker>;
    })}
  </>;
}
