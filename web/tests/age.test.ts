import assert from 'node:assert/strict';
import test from 'node:test';
import { gzipSync } from 'node:zlib';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { VillageFacts } from '../src/components/VillageAge';
import { StationAgeTable } from '../src/components/StationAge';
import Panel from '../src/components/Panel';
import StationInfo from '../src/components/StationInfo';
import type { AppState, MapData, StationSelection } from '../src/types';
import { createStationAgeLoader, loadCompressedJSON, parseStationAges, parseStationAgeShard, parseVillageAges, stationAgeShard } from '../src/ageLoader';
import { AGE_COLORS, AGE_UNKNOWN, catchmentGroupValue, groupFor, retryableLoader, villageAgeCollection,
  villageMeasure, type AgeCatchment, type AgeGroup, type AgeMetadata, type StationAgeCatchments, type VillageAgeProps, type VillageAges } from '../src/age';
import { readHash, writeHash } from '../src/hashState';

const groups: AgeGroup[] = [
  { key: 'age_0_14', label: '0–14 歲', min_age: 0, max_age: 14, set: 'three_stage' },
  { key: 'age_65_plus', label: '65 歲以上', min_age: 65, max_age: 100, set: 'three_stage' },
  { key: 'age_75_plus', label: '75 歲以上', min_age: 75, max_age: 100, set: 'planning' },
];
const metadata: AgeMetadata = { population_period: '2026-09', roster_period: '2026-09', boundary_vintage: '2026-08-17',
  same_month_geometry_verified: false, map_scope: '北北基', age_groups: groups,
  age_group_sets: { three_stage: ['age_0_14', 'age_65_plus'], planning: ['age_75_plus'] }, limitations: ['不同月里界'] };
const properties = (patch: Partial<VillageAgeProps> = {}): VillageAgeProps => ({
  area_id: '09007040002', boundary_id: '09007040002', boundary_join_status: 'verified', county_name: '連江縣',
  district_name: '東引鄉', area_name: '連江縣東引鄉', village_name: '樂華村', boundary_village_name: '樂華村',
  population: 100, age_counts: Array(101).fill(0), age_groups: { age_0_14: 0, age_65_plus: 25, age_75_plus: 10 }, ...patch,
});
const catchment = (patch: Partial<AgeCatchment> = {}): AgeCatchment => ({ radius_m: 350, status: 'complete',
  population_estimate: 100, population_known_estimate: 100,
  age_group_estimates: { age_65_plus: 25 }, age_group_known_estimates: { age_65_plus: 25 },
  boundary_share: 1, data_share: 1, population_data_share: 1, age_group_data_shares: { age_65_plus: 1 },
  outside_boundary_share: 0, village_count: 1, unknown_village_count: 0, unmatched_village_count: 0,
  missing_population_village_count: 0, geometry_conflict_village_count: 0, age_group_unknown_village_counts: { age_65_plus: 0 }, ...patch });

// Do not accidentally paint candidate population on an unverified geometry join.
test('unmatched village stays grey even when candidate ages are present', () => {
  const row = properties({ boundary_join_status: 'name_mismatch' });
  assert.deepEqual(villageMeasure(row, groups[1]), { count: null, share: null });
  const data: VillageAges = { type: 'FeatureCollection', metadata, features: [{ type: 'Feature', properties: row,
    geometry: { type: 'Polygon', coordinates: [[[121, 25], [122, 25], [122, 26], [121, 25]]] } }] };
  const layer = villageAgeCollection(data, groups[1]);
  assert.equal(layer.features[0].properties.color, AGE_UNKNOWN);
  assert.equal(layer.features[0].id, '09007040002');
  assert.equal(data.features[0].properties.boundary_join_status, 'name_mismatch');
});
test('a verified alias can use its explicitly checked population', () => {
  assert.deepEqual(villageMeasure(properties({ boundary_join_status: 'verified_alias' }), groups[1]), { count: 25, share: 0.25 });
});
test('known zero ages remain zero, missing ages and zero denominator stay unknown', () => {
  assert.deepEqual(villageMeasure(properties(), groups[0]), { count: 0, share: 0 });
  assert.deepEqual(villageMeasure(properties({ age_groups: { age_65_plus: null } }), groups[1]), { count: null, share: null });
  assert.deepEqual(villageMeasure(properties({ population: 0 }), groups[0]), { count: 0, share: null });
});
test('map uses fixed percentage breaks without modifying source geometry or values', () => {
  const row = properties();
  const data: VillageAges = { type: 'FeatureCollection', metadata, features: [{ type: 'Feature', properties: row,
    geometry: { type: 'Polygon', coordinates: [[[121, 25], [122, 25], [122, 26], [121, 25]]] } }] };
  const layer = villageAgeCollection(data, groups[1]);
  assert.equal(layer.features[0].properties.color, AGE_COLORS[2]);
  assert.equal(layer.features[0].geometry, data.features[0].geometry);
  assert.equal(row.age_groups.age_65_plus, 25);
});
test('group fallback remains in the selected non-overlapping set', () => {
  assert.equal(groupFor(metadata, 'unknown', 'three_stage').key, 'age_65_plus');
  assert.equal(groupFor(metadata, 'age_65_plus', 'planning').key, 'age_75_plus');
});
test('complete catchment uses the whole-circle total for the group percentage', () => {
  assert.deepEqual(catchmentGroupValue(catchment(), 'age_65_plus'), { count: 25, known: 25, coverage: 1, share: 0.25 });
});
test('partial catchment never presents a known subtotal as a whole-circle percentage', () => {
  assert.deepEqual(catchmentGroupValue(catchment({ status: 'partial', population_estimate: null,
    age_group_estimates: { age_65_plus: null }, age_group_known_estimates: { age_65_plus: 10 }, age_group_data_shares: { age_65_plus: 0.5 } }), 'age_65_plus'),
    { count: null, known: 10, coverage: 0.5, share: null });
  // Even a coverage value rounded to 100% cannot override the explicit partial status.
  assert.equal(catchmentGroupValue(catchment({ status: 'partial' }), 'age_65_plus').share, null);
});
test('no matching population is unknown; an observed complete zero stays zero', () => {
  assert.equal(catchmentGroupValue(catchment({ status: 'no_boundary', population_estimate: null,
    age_group_estimates: {}, age_group_known_estimates: {}, age_group_data_shares: {} }), 'age_65_plus').known, null);
  assert.deepEqual(catchmentGroupValue(catchment({ population_estimate: 0, age_group_estimates: { age_65_plus: 0 },
    age_group_known_estimates: { age_65_plus: 0 } }), 'age_65_plus'), { count: 0, known: 0, coverage: 1, share: null });
});
test('geometry conflict blocks percentages even if candidate counts exist', () => {
  assert.equal(catchmentGroupValue(catchment({ status: 'geometry_conflict' }), 'age_65_plus').share, null);
});
test('retry issues a fresh request after failure and shares successful requests', async () => {
  let calls = 0;
  const loader = retryableLoader(async () => { calls++; if (calls === 1) throw new Error('offline'); return 42; });
  const first = loader();
  assert.equal(loader(), first);
  await assert.rejects(first, /offline/);
  assert.equal(await loader(), 42);
  assert.equal(await loader(), 42);
  assert.equal(calls, 2);
});
test('age mode, group and set round-trip through shareable URL', () => {
  let hash = '#mode=village_age&ag=age_75_plus&aset=planning';
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    location: { get hash() { return hash; } }, history: { replaceState(_state: unknown, _unused: string, url: string) { hash = url; } },
  } });
  const state = readHash();
  assert.equal(state.mode, 'village_age'); assert.equal(state.ageGroup, 'age_75_plus'); assert.equal(state.ageSet, 'planning');
  writeHash(state, { center: [121.5, 25.05], zoom: 12 });
  const roundtrip = readHash();
  assert.equal(roundtrip.mode, state.mode); assert.equal(roundtrip.ageGroup, state.ageGroup); assert.equal(roundtrip.ageSet, state.ageSet);
});

test('gzip loading reduces transfer and preserves validated JSON values', async () => {
  const calls: string[] = [];
  const result = await loadCompressedJSON('ages.json', (value) => value, { fetcher: async (input) => {
    calls.push(String(input)); return new Response(gzipSync(JSON.stringify({ population: 0, missing: null })));
  } });
  assert.deepEqual(result, { population: 0, missing: null });
  assert.deepEqual(calls, ['ages.json.gz']);
});
test('a missing gzip file falls back to plain JSON; other HTTP failures do not', async () => {
  const calls: string[] = [];
  const fetcher: typeof fetch = async (input) => {
    calls.push(String(input)); return String(input).endsWith('.gz') ? new Response(null, { status: 404 }) : Response.json({ ok: true });
  };
  assert.deepEqual(await loadCompressedJSON('ages.json', (value) => value, { fetcher }), { ok: true });
  assert.deepEqual(calls, ['ages.json.gz', 'ages.json']);
  let failedCalls = 0;
  await assert.rejects(loadCompressedJSON('ages.json', (value) => value, { fetcher: async () => { failedCalls++; return new Response(null, { status: 503 }); } }), /503/);
  assert.equal(failedCalls, 1);
});
test('browsers without gzip stream support fetch only the plain fallback', async () => {
  const calls: string[] = [];
  assert.equal(await loadCompressedJSON('ages.json', (value) => value, { decompressor: null,
    fetcher: async (input) => { calls.push(String(input)); return Response.json(0); } }), 0);
  assert.deepEqual(calls, ['ages.json']);
});
test('gzip corruption and schema errors are retryable errors, never silent plain fallbacks', async () => {
  let calls = 0;
  await assert.rejects(loadCompressedJSON('ages.json', (value) => value, { fetcher: async () => { calls++; return new Response('not gzip'); } }));
  assert.equal(calls, 1);
  calls = 0;
  await assert.rejects(loadCompressedJSON('ages.json', parseVillageAges, { fetcher: async () => { calls++; return new Response(gzipSync(JSON.stringify({ type: 'FeatureCollection', features: [] }))); } }), /欄位或版本不符/);
  assert.equal(calls, 1);
});
test('village schema accepts null and zero while rejecting stale or ambiguous shape', () => {
  const data = { type: 'FeatureCollection', metadata, features: [{ type: 'Feature', properties: properties(), geometry: { type: 'Polygon', coordinates: [[[121, 25], [122, 25], [121, 25]]] } }] };
  assert.equal(parseVillageAges(data).features[0].properties.population, 100);
  assert.throws(() => parseVillageAges({ ...data, metadata: { ...metadata, age_groups: [] } }), /欄位或版本不符/);
  assert.throws(() => parseVillageAges({ ...data, features: [...data.features, ...data.features] }), /欄位或版本不符/);
  assert.throws(() => parseVillageAges({ ...data, features: [{ ...data.features[0], properties: { ...properties(), age_counts: [] } }] }), /欄位或版本不符/);
});

test('host-decoded gzip bodies are parsed once even when the gzip header remains', async () => {
  let calls = 0;
  const value = await loadCompressedJSON('ages.json', (data) => data, { fetcher: async () => {
    calls++; return new Response(JSON.stringify({ population: 0, missing: null }), { headers: { 'Content-Encoding': 'gzip' } });
  } });
  assert.deepEqual(value, { population: 0, missing: null });
  assert.equal(calls, 1);
});

test('official unassigned geometry may retain null names and a null age array', () => {
  const data = { type: 'FeatureCollection', metadata, features: [{ type: 'Feature', properties: properties({
    area_id: '10017010P01', boundary_id: '10017010P01', boundary_join_status: 'unassigned', village_name: null,
    boundary_village_name: null, population: null, age_counts: null,
    age_groups: Object.fromEntries(groups.map((group) => [group.key, null])),
  }), geometry: { type: 'Polygon', coordinates: [[[121, 25], [122, 25], [121, 25]]] } }] };
  const parsed = parseVillageAges(data);
  assert.equal(parsed.features[0].properties.village_name, null);
  assert.equal(villageMeasure(parsed.features[0].properties, groups[1]).share, null);
});

test('rendered village facts disclose source versions and keep unassigned people unknown', () => {
  const html = renderToStaticMarkup(createElement(VillageFacts, { properties: properties({ boundary_join_status: 'unassigned',
    village_name: null, boundary_village_name: null, population: null }), metadata, group: groups[1] }));
  assert.match(html, /未編定村里範圍/);
  assert.match(html, /資料未知/);
  assert.match(html, /2026-09/);
  assert.match(html, /2026-08-17/);
  assert.match(html, /尚未證明人口月與里界幾何同月一致/);
  assert.doesNotMatch(html, /25 人/);
});
test('rendered partial age table labels known subtotals and never displays a percentage', () => {
  const html = renderToStaticMarkup(createElement(StationAgeTable, { value: catchment({ status: 'partial', population_estimate: null,
    age_group_estimates: { age_65_plus: null }, age_group_known_estimates: { age_65_plus: 0 }, age_group_data_shares: { age_65_plus: 0.5 } }), groups: [groups[1]] }));
  assert.match(html, /已知部分人數/);
  assert.match(html, /約 0 人/);
  assert.match(html, /50%/); // Explicitly the separate area-coverage column.
  assert.doesNotMatch(html, /<small>[0-9]/);
});

test('unassigned boundary IDs are never labelled as population IDs in village facts', () => {
  const html = renderToStaticMarkup(createElement(VillageFacts, { properties: properties({
    area_id: '10017010P01', boundary_id: '10017010P01', boundary_join_status: 'unassigned',
    village_name: null, boundary_village_name: null, population: null, age_counts: null,
    age_groups: Object.fromEntries(groups.map((group) => [group.key, null])),
  }), metadata, group: groups[1] }));
  assert.match(html, /人口碼：無可對應人口碼/);
  assert.match(html, /里界碼 10017010P01/);
  assert.doesNotMatch(html, /人口碼：10017010P01/);
  const matched = renderToStaticMarkup(createElement(VillageFacts, { properties: properties(), metadata, group: groups[1] }));
  assert.match(matched, /人口碼：09007040002/);
});


const shardData = (index: number): StationAgeCatchments => ({ schema_version: 1, dataset_id: 'web_station_age_catchments', radii: [350],
  metadata: { ...metadata, shard: { algorithm: 'fnv1a32-utf8-mod32', index, count: 32, station_count: 0 } },
  records: { bus: {}, mrt: {}, youbike: {} },
});
test('station shard hash matches the builder for source namespaces, NWT IDs and UTF-8', () => {
  for (const [kind, id, expected] of [
    ['bus', '100', 24], ['mrt', '100', 31], ['youbike', '100', 24], ['bus', 'NWT100', 13],
    ['bus', 'NWT200:121.5:25.0', 14], ['bus', '臺北站', 13], ['bus', '😀', 4],
  ] as const) assert.equal(stationAgeShard(kind, id), expected, `${kind}:${id}`);
});
test('shard loads require the requested algorithm, index, count and actual station count', () => {
  const data = shardData(13);
  const entry = catchment({
    age_group_estimates: { age_0_14: 0, age_65_plus: 25, age_75_plus: 10 },
    age_group_known_estimates: { age_0_14: 0, age_65_plus: 25, age_75_plus: 10 },
    age_group_data_shares: { age_0_14: 1, age_65_plus: 1, age_75_plus: 1 },
    age_group_unknown_village_counts: { age_0_14: 0, age_65_plus: 0, age_75_plus: 0 },
  });
  data.records.bus.NWT100 = [entry]; data.metadata.shard!.station_count = 1;
  assert.equal(parseStationAgeShard(data, 13).records.bus.NWT100[0].population_estimate, 100);
  assert.throws(() => parseStationAgeShard(data, 14), /欄位或版本不符/);
  for (const patch of [{ algorithm: 'old' }, { count: 16 }, { station_count: 2 }]) {
    assert.throws(() => parseStationAgeShard({ ...data, metadata: { ...data.metadata, shard: { ...data.metadata.shard, ...patch } } }, 13), /欄位或版本不符/);
  }
  const canonical = { ...data, metadata: { ...data.metadata, shard: undefined } };
  assert.equal(parseStationAges(canonical).records.bus.NWT100.length, 1);
  assert.throws(() => parseStationAgeShard(canonical, 13), /欄位或版本不符/);
});
test('station loading uses original NWT IDs and retains only four least-recently-used shards', async () => {
  const calls: { url: string; index: number }[] = [];
  const load = createStationAgeLoader('/base/data/', async (url, index) => { calls.push({ url, index }); return shardData(index); });
  await load('bus', '100'); await load('mrt', '100'); await load('bus', 'NWT100'); await load('bus', 'NWT200:121.5:25.0');
  await load('bus', '100'); // Touch 24 so 31 becomes least recently used.
  await load('bus', '😀'); await load('bus', 'NWT100'); await load('mrt', '100');
  assert.equal(calls.length, 6);
  assert.equal(calls.filter((entry) => entry.index === 31).length, 2);
  assert.equal(calls.filter((entry) => entry.index === 13).length, 1);
  assert.equal(calls.find((entry) => entry.index === 13)!.url, '/base/data/station_age_catchments/part-13.json');
  assert.equal(calls.some((entry) => entry.url.endsWith('/station_age_catchments.json')), false);
});
test('same-shard requests share one fetch and failed shards can be retried', async () => {
  let calls = 0;
  const load = createStationAgeLoader('/data/', async (_url, index) => { calls++; if (calls === 1) throw new Error('offline'); return shardData(index); });
  const first = load('bus', '100');
  assert.equal(load('youbike', '100'), first); // Both are shard 24, distinct source namespaces retained inside it.
  await assert.rejects(first, /offline/);
  assert.equal((await load('bus', '100')).metadata.shard!.index, 24);
  assert.equal(calls, 2);
});
test('village age legend remains visible when the general village overlay is selected', () => {
  const state: AppState = { ...readHash(), mode: 'village_age', ageGroup: 'age_65_plus', ageSet: 'three_stage', layers: new Set(['villages']) };
  const html = renderToStaticMarkup(createElement(Panel, { state, villageAges: { type: 'FeatureCollection', metadata, features: [] },
    ageError: null, onRetryAges() {}, selectedVillage: null, onVillage() {}, playing: false, needCounts: null,
    data: null, supplementalPending: new Set(), onRetrySupplemental() {}, onChange() {}, onTogglePlay() {}, onPreferences() {},
  }));
  assert.match(html, /65 歲以上占村里戶籍人口比例/);
  assert.match(html, /一般里人口填色暫藏/);
  assert.doesNotMatch(html, /每里戶籍人口（人）/);
});

test('regional station statistical-area copy describes inapplicability rather than an unknown period', () => {
  const layers = ['roads', 'districts', 'taipei_boundary', 'bus_routes', 'bus_stops', 'mrt_lines', 'mrt_stations', 'district_labels', 'places', 'youbike_stations'];
  const data = { ...Object.fromEntries(layers.map((key) => [key, { type: 'FeatureCollection', features: [] }])),
    regional_bus_routes: null, regional_bus_stops: null, villages: null, traffic_sections: null,
    bus_travel_times: null, supplemental_manifest: null, supplemental_errors: [],
  } as unknown as MapData;
  const station: StationSelection = { type: 'bus', id: 'NWT20147', at: [121.49, 24.99],
    properties: { id: 'NWT20147', name: '圓通路305巷', route_count: 1, routes: ['測試路線'], weekday_departures: null, source_scope: 'NewTaipei' } };
  const html = renderToStaticMarkup(createElement(StationInfo, { station, mapRef: { current: null }, data, placeKeys: new Set(), onClose() {} }));
  assert.match(html, /統計區年齡分布（此站不適用）/);
  assert.match(html, /上方統計區來源未涵蓋此新北站位；下方村里資料各自保留來源與月份，不混算/);
  assert.doesNotMatch(html, /統計區年齡分布（期間未知）/);
  assert.doesNotMatch(html, /此卡使用最小統計區人口/);
});
