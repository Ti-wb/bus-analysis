import { useMemo } from 'react';
import { Layer, Source } from 'react-map-gl/maplibre';
import type { FeatureCollection, LineString, Point, Polygon } from 'geojson';
import type { ExpressionSpecification } from 'maplibre-gl';
import { circle } from '../catchment';
import { routePath } from './engine';
import type { ExistingService } from './existingServices';
import type { ScenarioData } from './types';

type ServiceStatus = 'running' | 'zero' | 'unknown';
interface ServiceProperties { status: ServiceStatus; emphasized: boolean }
interface Props {
  data: ScenarioData;
  services: ExistingService[];
  selectedStopIds: string[];
  highlightedRouteId: string | null;
  beforeId: string;
}

const STATUS_RANK: Record<ServiceStatus, number> = { zero: 0, unknown: 1, running: 2 };
const SERVICE_STYLES: { status: ServiceStatus; color: string; opacity: number; dash?: number[] }[] = [
  { status: 'zero', color: '#8c939b', opacity: .64, dash: [3, 2] },
  { status: 'unknown', color: '#91aaa9', opacity: .5, dash: [1, 2.5] },
  { status: 'running', color: '#168b89', opacity: .76 },
];
const STOP_COLOR: ExpressionSpecification = ['match', ['get', 'status'],
  'running', '#168b89', 'zero', '#8c939b', '#91aaa9'];

function serviceStatus(frequency: number | null): ServiceStatus {
  if (frequency === null || !Number.isFinite(frequency) || frequency < 0) return 'unknown';
  return frequency > 0 ? 'running' : 'zero';
}

/** Context only: these layers are deliberately absent from map hit testing. */
export default function ExistingServiceLayers({ data, services, selectedStopIds, highlightedRouteId, beforeId }: Props) {
  const collections = useMemo(() => {
    const selected = new Set(selectedStopIds);
    const emphasizedAreas = new Set<string>();
    const boarding = new Map<string, { status: ServiceStatus; emphasizedStatus: ServiceStatus | null }>();
    const routes: FeatureCollection<LineString, ServiceProperties & { route_id: string; variant_id: string }> = {
      type: 'FeatureCollection', features: [],
    };

    for (const service of services) {
      const emphasized = highlightedRouteId === null || service.route.route_id === highlightedRouteId;
      const status = serviceStatus(service.frequency);
      const coordinates = routePath(service.route, data);
      if (coordinates.length > 1) routes.features.push({
        type: 'Feature', id: service.route.id,
        properties: { route_id: service.route.route_id, variant_id: service.route.id, status, emphasized },
        geometry: { type: 'LineString', coordinates },
      });
      for (const match of service.areaMatches) {
        if (emphasized) emphasizedAreas.add(match.selectedStopId);
        for (const stopId of match.boardingStopIds) {
          // Numbered draft stops already mark these locations more clearly.
          if (selected.has(stopId)) continue;
          const previous = boarding.get(stopId);
          boarding.set(stopId, {
            status: !previous || STATUS_RANK[status] > STATUS_RANK[previous.status] ? status : previous.status,
            emphasizedStatus: emphasized
              ? !previous?.emphasizedStatus || STATUS_RANK[status] > STATUS_RANK[previous.emphasizedStatus] ? status : previous.emphasizedStatus
              : previous?.emphasizedStatus ?? null,
          });
        }
      }
    }

    const areas: FeatureCollection<Polygon, { selected_stop_id: string; emphasized: boolean }> = {
      type: 'FeatureCollection',
      features: [...selected].flatMap((id) => {
        const stop = data.records.stops[id];
        return stop ? [{ ...circle(stop.at, 350), id,
          properties: { selected_stop_id: id, emphasized: highlightedRouteId === null || emphasizedAreas.has(id) } }] : [];
      }),
    };
    const stops: FeatureCollection<Point, ServiceProperties & { stop_id: string }> = {
      type: 'FeatureCollection',
      features: [...boarding].flatMap(([id, state]) => {
        const stop = data.records.stops[id];
        return stop ? [{ type: 'Feature' as const, id,
          properties: { stop_id: id, status: state.emphasizedStatus ?? state.status, emphasized: state.emphasizedStatus !== null },
          geometry: { type: 'Point' as const, coordinates: stop.at } }] : [];
      }),
    };
    return { areas, routes, stops };
  }, [data, services, selectedStopIds, highlightedRouteId]);

  return <>
    <Source id="scenario-service-areas" type="geojson" data={collections.areas}>
      <Layer id="scenario-service-area-fill" type="fill" beforeId={beforeId}
        paint={{ 'fill-color': '#168b89', 'fill-opacity': ['case', ['get', 'emphasized'], .045, .015] }} />
      <Layer id="scenario-service-area-line" type="line" beforeId={beforeId}
        paint={{ 'line-color': '#168b89', 'line-width': 1, 'line-dasharray': [3, 3],
          'line-opacity': ['case', ['get', 'emphasized'], .48, .16] }} />
    </Source>
    <Source id="scenario-existing-services" type="geojson" data={collections.routes}>
      {[false, true].flatMap((emphasized) => SERVICE_STYLES.map(({ status, color, opacity, dash }) => (
        <Layer key={`${status}:${emphasized}`} id={`scenario-service-${status}-${emphasized ? 'emphasis' : 'muted'}`}
          type="line" beforeId={beforeId}
          filter={['all', ['==', ['get', 'status'], status], ['==', ['get', 'emphasized'], emphasized]]}
          layout={{ 'line-cap': dash ? 'butt' : 'round', 'line-join': 'round' }}
          paint={{ 'line-color': color,
            'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.2, 15, emphasized && highlightedRouteId !== null ? 3.1 : 2.1],
            'line-opacity': emphasized ? Math.min(1, opacity + (highlightedRouteId !== null ? .15 : 0)) : .12,
            ...(dash ? { 'line-dasharray': dash } : {}) }} />
      )))}
    </Source>
    <Source id="scenario-service-stops" type="geojson" data={collections.stops}>
      <Layer id="scenario-service-boarding-stops" type="circle" beforeId={beforeId}
        paint={{ 'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 13, 4.5, 15, 8.5, 17, 10],
          'circle-color': STOP_COLOR, 'circle-opacity': 0,
          'circle-stroke-color': STOP_COLOR, 'circle-stroke-width': 1.5,
          'circle-stroke-opacity': ['case', ['get', 'emphasized'], .75, .14] }} />
    </Source>
  </>;
}
