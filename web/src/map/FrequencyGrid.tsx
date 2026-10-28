import { useEffect, useMemo, useRef } from 'react';
import { Layer, Source, useMap } from 'react-map-gl/maplibre';
import type { GeoJSONSource, GeoJSONSourceDiff } from 'maplibre-gl';
import { gridClasses, gridCollection } from '../data';
import type { AppState, Grid } from '../types';
import { gridFillLayer, gridHatchLayer } from './layers';

/** Keep geometry in the map worker; time changes send only changed colour classes. */
export default function FrequencyGrid({ grid, state, visible, beforeId }: {
  grid: Grid; state: AppState; visible: boolean; beforeId?: string;
}) {
  const { current: map } = useMap();
  const initial = useMemo(() => {
    const data = gridCollection(grid, state);
    return { data, classes: Int8Array.from(data.features, (feature) => feature.properties.k) };
    // Capture the current time only when loading a different dataset.
  }, [grid]);
  const classes = useMemo(() => gridClasses(grid, state), [grid, state.cell, state.day, state.hour]);
  const previous = useRef<{ source: GeoJSONSource; initial: typeof initial; classes: Int8Array } | null>(null);
  useEffect(() => {
    const source = map?.getSource<GeoJSONSource>('grid');
    if (!source) return;
    const old = previous.current?.source === source && previous.current.initial === initial ? previous.current.classes : initial.classes;
    const update: NonNullable<GeoJSONSourceDiff['update']> = [];
    classes.forEach((k, id) => { if (k !== old[id]) update.push({ id, addOrUpdateProperties: [{ key: 'k', value: k }] }); });
    previous.current = { source, initial, classes };
    if (update.length) void source.updateData({ update }).catch((error: unknown) => {
      map?.getMap().fire('error', { error: error instanceof Error ? error : new Error(String(error)), sourceId: 'grid' });
    });
  }, [map, initial, classes]);
  const layout = { visibility: visible ? 'visible' as const : 'none' as const };
  return <Source id="grid" type="geojson" data={initial.data}>
    <Layer {...gridFillLayer} beforeId={beforeId} layout={layout} />
    <Layer {...gridHatchLayer} beforeId={beforeId} layout={layout} />
  </Source>;
}
