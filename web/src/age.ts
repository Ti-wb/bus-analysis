import type { FeatureCollection, MultiPolygon, Polygon } from 'geojson';
import type { StationType } from './types';

export interface AgeGroup {
  key: string;
  label: string;
  min_age: number;
  /** 100 includes all people aged 100 and over. */
  max_age: number;
  set: 'three_stage' | 'planning';
}

export interface AgeMetadata {
  population_period: string;
  roster_period: string;
  boundary_vintage: string;
  same_month_geometry_verified: boolean;
  map_scope: string;
  age_groups: AgeGroup[];
  age_group_sets: Record<AgeGroup['set'], string[]>;
  national_audit_summary?: {
    population_village_count: number;
    roster_village_count: number;
    boundary_feature_count: number;
    excluded_population_village_count: number;
    excluded_source_population_known_sum: number;
    boundary_status_counts: Record<string, number>;
  };
  limitations: string[];
}

export interface VillageAgeProps {
  area_id: string;
  boundary_id: string;
  boundary_join_status: string;
  county_name: string;
  district_name: string;
  area_name: string;
  village_name: string | null;
  boundary_village_name: string | null;
  population: number | null;
  age_counts: (number | null)[] | null;
  age_groups: Record<string, number | null>;
}

export type VillageAges = FeatureCollection<Polygon | MultiPolygon, VillageAgeProps> & { metadata: AgeMetadata };
export interface VillageAgeMapProps { area_id: string; boundary_id: string; color: string; share: number | null }
export type VillageAgeMap = FeatureCollection<Polygon | MultiPolygon, VillageAgeMapProps>;

export interface AgeCatchment {
  radius_m: number;
  status: 'complete' | 'partial' | 'no_boundary' | 'geometry_conflict';
  population_estimate: number | null;
  population_known_estimate: number | null;
  age_group_estimates: Record<string, number | null>;
  age_group_known_estimates: Record<string, number | null>;
  boundary_share: number;
  data_share: number;
  population_data_share: number;
  age_group_data_shares: Record<string, number>;
  outside_boundary_share: number;
  village_count: number;
  unknown_village_count: number;
  unmatched_village_count: number;
  missing_population_village_count: number;
  geometry_conflict_village_count: number;
  age_group_unknown_village_counts: Record<string, number>;
}
export interface StationAgeCatchments {
  schema_version: number;
  dataset_id: string;
  radii: number[];
  metadata: AgeMetadata;
  records: Record<StationType, Record<string, AgeCatchment[]>>;
}

export const AGE_COLORS = ['#edf5e9', '#c9e3bd', '#8bc49c', '#479b82', '#176b62'];
export const AGE_UNKNOWN = '#a9afb4';
/** Fixed, shared percentage breaks keep changes of age band comparable. */
export const AGE_BREAKS = [0, 0.1, 0.2, 0.3, 0.4];

export function isVerifiedVillage(status: string): boolean {
  return status === 'verified' || status === 'verified_alias';
}

export function joinStatusText(status: string): string {
  const labels: Record<string, string> = {
    verified: '代碼與名稱核對一致', verified_alias: '已核對名稱異體字',
    name_mismatch: '代碼相同、名稱未匹配', missing_population: '缺少人口資料',
    unassigned: '里界無可對應的村里', population_only: '人口資料未匹配里界',
  };
  return labels[status] ?? `未完成連接（${status}）`;
}

export function groupFor(metadata: AgeMetadata, key: string, set?: AgeGroup['set']): AgeGroup {
  const groups = set ? metadata.age_groups.filter((group) => group.set === set) : metadata.age_groups;
  return groups.find((group) => group.key === key)
    ?? groups.find((group) => group.min_age === 65 && group.max_age === 100)
    ?? groups[0];
}

/** Use the official total as denominator; unknown cells and zero denominators stay unknown. */
export function villageMeasure(properties: VillageAgeProps, group: AgeGroup): { count: number | null; share: number | null } {
  if (!isVerifiedVillage(properties.boundary_join_status)) return { count: null, share: null };
  const count = properties.age_groups[group.key];
  const knownCount = typeof count === 'number' && Number.isFinite(count) && count >= 0 ? count : null;
  const total = properties.population;
  return { count: knownCount, share: knownCount !== null && total !== null && total > 0 ? knownCount / total : null };
}

export function villageAgeCollection(data: VillageAges, group: AgeGroup): VillageAgeMap {
  return { type: 'FeatureCollection', features: data.features.map((feature) => {
    const { share } = villageMeasure(feature.properties, group);
    let index = 0;
    if (share !== null) AGE_BREAKS.forEach((threshold, i) => { if (share >= threshold) index = i; });
    return { type: 'Feature', id: feature.properties.boundary_id, geometry: feature.geometry,
      properties: { area_id: feature.properties.area_id, boundary_id: feature.properties.boundary_id,
        color: share === null ? AGE_UNKNOWN : AGE_COLORS[index], share } };
  }) };
}

/** A whole-circle percentage requires complete geometry and complete data for that group. */
export function catchmentGroupValue(value: AgeCatchment, key: string) {
  const count = value.age_group_estimates[key] ?? null;
  const known = value.age_group_known_estimates[key] ?? null;
  const coverage = value.age_group_data_shares[key] ?? 0;
  const total = value.population_estimate;
  const complete = value.status === 'complete' && coverage >= 1 - 1e-6;
  return { count: complete ? count : null, known, coverage,
    share: complete && count !== null && total !== null && total > 0 ? count / total : null };
}

export const percentText = (share: number) => `${(share * 100).toLocaleString('zh-TW', { maximumFractionDigits: 1 })}%`;
export const estimateText = (value: number) => value.toLocaleString('zh-TW', { maximumFractionDigits: 1 });

/** Share in-flight/successful requests, but never cache failure: the retry button must issue a new request. */
export function retryableLoader<T>(load: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | null = null;
  return () => {
    pending ??= load().catch((error: unknown) => { pending = null; throw error; });
    return pending;
  };
}

/** Bounding-box centre is used only to position the view, never for population calculations. */
export function villageCenter(feature: VillageAges['features'][number]): [number, number] {
  const coordinates = feature.geometry.type === 'Polygon'
    ? feature.geometry.coordinates.flat() : feature.geometry.coordinates.flat(2);
  const xs = coordinates.map((point) => point[0]);
  const ys = coordinates.map((point) => point[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}
