import type { AgeMetadata, StationAgeCatchments, VillageAges } from './age';
import type { StationType } from './types';

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const nonnegative = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const count = (value: unknown): boolean => value === null || nonnegative(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((entry) => typeof entry === 'string');
function requireValue(condition: unknown): asserts condition {
  if (!condition) throw new Error('年齡資料欄位或版本不符，請重新建置資料後再載入。');
}
function metadataOf(value: unknown): AgeMetadata {
  requireValue(object(value));
  for (const key of ['population_period', 'roster_period', 'boundary_vintage', 'map_scope']) requireValue(typeof value[key] === 'string');
  requireValue(typeof value.same_month_geometry_verified === 'boolean' && strings(value.limitations));
  requireValue(Array.isArray(value.age_groups) && value.age_groups.length > 0);
  for (const group of value.age_groups) {
    requireValue(object(group) && typeof group.key === 'string' && typeof group.label === 'string');
    requireValue(Number.isInteger(group.min_age) && Number.isInteger(group.max_age)
      && nonnegative(group.min_age) && nonnegative(group.max_age) && group.min_age <= group.max_age && group.max_age <= 100);
    requireValue(group.set === 'three_stage' || group.set === 'planning');
  }
  requireValue(object(value.age_group_sets));
  for (const set of ['three_stage', 'planning']) {
    requireValue(strings(value.age_group_sets[set]) && value.age_group_sets[set].length > 0);
    const actual = value.age_groups.filter((group) => group.set === set).map((group) => group.key);
    requireValue(actual.length > 0 && new Set(actual).size === actual.length
      && actual.join(',') === value.age_group_sets[set].join(','));
  }
  if (value.national_audit_summary !== undefined) {
    const summary = value.national_audit_summary;
    requireValue(object(summary) && object(summary.boundary_status_counts));
    for (const key of ['population_village_count', 'roster_village_count', 'boundary_feature_count', 'excluded_population_village_count', 'excluded_source_population_known_sum']) requireValue(nonnegative(summary[key]));
  }
  return value as unknown as AgeMetadata;
}
export function parseVillageAges(value: unknown): VillageAges {
  requireValue(object(value) && value.type === 'FeatureCollection' && Array.isArray(value.features));
  const metadata = metadataOf(value.metadata);
  const ids = new Set<string>();
  for (const feature of value.features) {
    requireValue(object(feature) && feature.type === 'Feature' && object(feature.geometry));
    requireValue((feature.geometry.type === 'Polygon' || feature.geometry.type === 'MultiPolygon') && Array.isArray(feature.geometry.coordinates));
    const properties = feature.properties;
    requireValue(object(properties));
    for (const key of ['area_id', 'boundary_id', 'boundary_join_status', 'county_name', 'district_name', 'area_name']) requireValue(typeof properties[key] === 'string');
    for (const key of ['village_name', 'boundary_village_name']) requireValue(properties[key] === null || typeof properties[key] === 'string');
    requireValue(!ids.has(properties.boundary_id as string));
    ids.add(properties.boundary_id as string);
    requireValue(count(properties.population) && (properties.age_counts === null || (Array.isArray(properties.age_counts) && properties.age_counts.length === 101 && properties.age_counts.every(count))));
    requireValue(object(properties.age_groups));
    for (const group of metadata.age_groups) requireValue(count(properties.age_groups[group.key]));
  }
  return value as unknown as VillageAges;
}
export function parseStationAges(value: unknown): StationAgeCatchments {
  requireValue(object(value) && value.schema_version === 1 && value.dataset_id === 'web_station_age_catchments');
  const metadata = metadataOf(value.metadata);
  requireValue(Array.isArray(value.radii) && value.radii.includes(350) && value.radii.every(nonnegative) && object(value.records));
  for (const kind of ['bus', 'mrt', 'youbike']) {
    const stations = value.records[kind];
    requireValue(object(stations));
    for (const entries of Object.values(stations)) {
      requireValue(Array.isArray(entries) && entries.length === value.radii.length);
      entries.forEach((entry, index) => {
        requireValue(object(entry) && entry.radius_m === (value.radii as number[])[index]);
        requireValue(['complete', 'partial', 'no_boundary', 'geometry_conflict'].includes(String(entry.status)));
        requireValue(count(entry.population_estimate) && count(entry.population_known_estimate));
        for (const field of ['boundary_share', 'data_share', 'population_data_share', 'outside_boundary_share']) requireValue(nonnegative(entry[field]) && entry[field] <= 1);
        for (const field of ['village_count', 'unknown_village_count', 'unmatched_village_count', 'missing_population_village_count', 'geometry_conflict_village_count']) requireValue(nonnegative(entry[field]) && Number.isInteger(entry[field]));
        for (const field of ['age_group_estimates', 'age_group_known_estimates', 'age_group_data_shares', 'age_group_unknown_village_counts']) requireValue(object(entry[field]));
        for (const group of metadata.age_groups) {
          requireValue(count((entry.age_group_estimates as Record<string, unknown>)[group.key]) && count((entry.age_group_known_estimates as Record<string, unknown>)[group.key]));
          const share = (entry.age_group_data_shares as Record<string, unknown>)[group.key];
          requireValue(nonnegative(share) && share <= 1);
          requireValue(nonnegative((entry.age_group_unknown_village_counts as Record<string, unknown>)[group.key]));
        }
      });
    }
  }
  return value as unknown as StationAgeCatchments;
}

/** Only an absent gzip file (404) or unavailable browser decompressor uses plain JSON. */
export async function loadCompressedJSON<T>(url: string, validate: (value: unknown) => T, runtime: {
  fetcher?: typeof fetch; decompressor?: typeof DecompressionStream | null;
} = {}): Promise<T> {
  const fetcher = runtime.fetcher ?? fetch;
  const Decompressor = runtime.decompressor === null ? undefined : runtime.decompressor ?? globalThis.DecompressionStream;
  const plain = async () => {
    const response = await fetcher(url);
    if (!response.ok) throw new Error(`年齡資料載入失敗：HTTP ${response.status}`);
    return validate(await response.json());
  };
  if (!Decompressor) return plain();
  const compressed = await fetcher(`${url}.gz`);
  if (compressed.status === 404) return plain();
  if (!compressed.ok) throw new Error(`年齡壓縮資料載入失敗：HTTP ${compressed.status}`);
  const bytes = new Uint8Array(await compressed.arrayBuffer());
  if (!bytes.length) throw new Error('年齡壓縮資料為空白。');
  // A host may have already decoded Content-Encoding: gzip before fetch exposes the body.
  // Inspect the bytes rather than trusting the response header and decoding twice.
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    return validate(await new Response(new Response(bytes).body!.pipeThrough(new Decompressor('gzip'))).json());
  }
  return validate(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)));
}


export const STATION_AGE_SHARDS = 32;
/** Shared with the builder: hash the source namespace and original station ID as UTF-8 bytes. */
export function stationAgeShard(kind: StationType, id: string): number {
  let hash = 2166136261;
  for (const byte of new TextEncoder().encode(`${kind}:${id}`)) hash = Math.imul(hash ^ byte, 16777619) >>> 0;
  return hash % STATION_AGE_SHARDS;
}

export function parseStationAgeShard(value: unknown, index: number): StationAgeCatchments {
  const data = parseStationAges(value);
  const shard = data.metadata.shard;
  requireValue(shard && shard.algorithm === 'fnv1a32-utf8-mod32' && shard.index === index
    && shard.count === STATION_AGE_SHARDS && Number.isInteger(shard.station_count) && shard.station_count >= 0);
  let stations = 0;
  for (const kind of ['bus', 'mrt', 'youbike'] as const) {
    const ids = Object.keys(data.records[kind]);
    requireValue(ids.every((id) => stationAgeShard(kind, id) === index));
    stations += ids.length;
  }
  requireValue(stations === shard.station_count);
  return data;
}

/** At most four decompressed station shards are retained, including in-flight requests. */
export function createStationAgeLoader(baseURL: string, read: (url: string, index: number) => Promise<StationAgeCatchments>
  = (url, index) => loadCompressedJSON(url, (value) => parseStationAgeShard(value, index))) {
  const cache = new Map<number, Promise<StationAgeCatchments>>();
  return (kind: StationType, id: string): Promise<StationAgeCatchments> => {
    const index = stationAgeShard(kind, id);
    const cached = cache.get(index);
    if (cached) { cache.delete(index); cache.set(index, cached); return cached; }
    const url = `${baseURL}station_age_catchments/part-${String(index).padStart(2, '0')}.json`;
    const pending = Promise.resolve().then(() => read(url, index)).catch((error: unknown) => {
      if (cache.get(index) === pending) cache.delete(index);
      throw error;
    });
    cache.set(index, pending);
    if (cache.size > 4) cache.delete(cache.keys().next().value!);
    return pending;
  };
}
