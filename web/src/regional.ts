import type { FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson';
import { ALL_DAY } from './config';
import type { AppState, BusTravelTimeProps, SupplementalData, SupplementalRecord, VillageProps } from './types';

function inRing(point: Position, ring: Position[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > point[1]) !== (yj > point[1]) && point[0] < (xj - xi) * (point[1] - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function inPolygon(point: Position, rings: Position[][]): boolean {
  return !!rings[0] && inRing(point, rings[0]) && !rings.slice(1).some((ring) => inRing(point, ring));
}

/** Administrative context only: no area apportionment or station-catchment estimate. */
export function villageAt(villages: FeatureCollection<Polygon | MultiPolygon, VillageProps> | null, at: Position): VillageProps | null {
  const feature = villages?.features.find((f) => f.geometry.type === 'Polygon'
    ? inPolygon(at, f.geometry.coordinates)
    : f.geometry.coordinates.some((polygon) => inPolygon(at, polygon)));
  return feature?.properties ?? null;
}

export function travelValues(p: BusTravelTimeProps, state: Pick<AppState, 'day' | 'hour'>) {
  const series = state.day === 'weekday' ? p.weekday : p.weekend;
  const values = series.filter((value): value is number => value !== null && Number.isFinite(value) && value >= 0);
  return {
    seconds: state.hour === ALL_DAY ? null : series[state.hour] ?? null,
    min_seconds: values.length ? Math.min(...values) : null,
    max_seconds: values.length ? Math.max(...values) : null,
    available_hours: values.length,
  };
}

export function travelCollection(data: SupplementalData['bus_travel_times'], state: Pick<AppState, 'day' | 'hour'>) {
  if (!data) return null;
  return {
    ...data,
    features: data.features.map((feature) => ({ ...feature, properties: { ...feature.properties, ...travelValues(feature.properties, state) } })),
  };
}

export function secondsText(seconds: unknown): string {
  return typeof seconds === 'number' && Number.isFinite(seconds) ? `${seconds.toLocaleString('zh-TW', { maximumFractionDigits: 1 })} 秒` : '無可用數值';
}

export const countText = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-TW') : '無資料';
export const percentText = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '無資料';

export function snapshotPeriodText(record: SupplementalRecord): string {
  if (typeof record.period === 'string') return `統計期 ${record.period}`;
  if (record.period && typeof record.period === 'object') {
    const period = record.period as Record<string, unknown>;
    if (period.data_collect_time_start || period.data_collect_time_end) return `觀測快照 ${period.data_collect_time_start ?? '未提供'}～${period.data_collect_time_end ?? '未提供'}`;
    if (Array.isArray(period.effective_dates) || Array.isArray(period.expire_dates)) {
      return `有效期 ${Array.isArray(period.effective_dates) ? period.effective_dates.join('、') : '未提供'}～${Array.isArray(period.expire_dates) ? period.expire_dates.join('、') : '未提供'}`;
    }
    const start = period.start ?? period.effective_date;
    const end = period.end ?? period.expire_date;
    if (start || end) return `統計／有效期間 ${start ?? '未提供'}～${end ?? '未提供'}`;
  }
  return record.source_updated_at ? `來源更新 ${record.source_updated_at}` : '來源更新時間未提供';
}

export function villagePeriod(data: SupplementalData): string | null {
  const value = data.villages?.metadata?.period ?? data.supplemental_manifest?.records.find((record) => record.id === 'villages')?.period;
  return typeof value === 'string' ? value : null;
}
