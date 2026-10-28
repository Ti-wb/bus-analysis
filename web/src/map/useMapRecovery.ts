import { useCallback, useEffect, useReducer, useState, type RefObject } from 'react';
import type { PaddingOptions, StyleSpecification } from 'maplibre-gl';
import type { MapRef } from 'react-map-gl/maplibre';
import type { ViewState } from '../types';
import { INITIAL_MAP_LOAD, MAP_LOAD_TIMEOUT, mapLoadReducer, type Basemap } from './loadState';

export const LOCAL_STYLE: StyleSpecification = {
  version: 8, sources: {}, layers: [{ id: 'local-background', type: 'background', paint: { 'background-color': '#eef3f7' } }],
};

/** Keep data and selection outside map recovery; only replace the failed renderer. */
export function useMapRecovery(mapRef: RefObject<MapRef | null>) {
  const [mapLoad, dispatchMapLoad] = useReducer(mapLoadReducer, INITIAL_MAP_LOAD);
  const [fallbackView, setFallbackView] = useState<(ViewState & { bearing: number; pitch: number; padding: PaddingOptions }) | null>(null);
  const retainCamera = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const center = map.getCenter();
    setFallbackView({ center: [center.lng, center.lat], zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch(), padding: map.getPadding() });
  }, [mapRef]);
  const retryMap = useCallback((base: Basemap) => {
    retainCamera();
    dispatchMapLoad({ type: 'retry', base });
  }, [retainCamera]);

  useEffect(() => {
    if (mapLoad.status !== 'loading') return;
    const timer = setTimeout(() => {
      retainCamera();
      dispatchMapLoad({ type: 'timeout', attempt: mapLoad.attempt });
    }, MAP_LOAD_TIMEOUT);
    return () => clearTimeout(timer);
  }, [mapLoad.status, mapLoad.attempt, retainCamera]);
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map || mapLoad.status !== 'ready') return;
    const lost = () => {
      retainCamera();
      dispatchMapLoad({ type: 'context-lost', attempt: mapLoad.attempt });
    };
    map.on('webglcontextlost', lost);
    return () => { map.off('webglcontextlost', lost); };
  }, [mapRef, mapLoad.status, mapLoad.attempt, retainCamera]);

  return { mapLoad, dispatchMapLoad, fallbackView, retryMap };
}
