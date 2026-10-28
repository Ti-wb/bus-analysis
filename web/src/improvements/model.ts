import { DEFAULTS, KINDS, PERIODS, PROBLEMS, type Entry, type Filters, type Ranked, type SortKey } from './types';

const clamp = (n: number, a = 0, b = 1) => Math.max(a, Math.min(b, n));
const round = (n: number, digits = 1) => Number(n.toFixed(digits));
/** Stable synthetic values, independent of row ordering, search and pagination. Never actual observations. */
export function sample(key: string): number {
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) h = Math.imul(h ^ key.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
}
export function mean(series: number[] | null, start: number, end: number): number | null {
  return series ? series.slice(start, end).reduce((a, b) => a + b, 0) / (end - start) : null;
}

export function rankEntries(entries: Entry[], f: Pick<Filters, 'day' | 'period' | 'weights' | 'overlap' | 'passengers'>): Ranked[] {
  const { start, end } = PERIODS[f.period];
  const freq = entries.map((r) => mean(r.hourly[f.day], start, end));
  const sorted = freq.filter((n): n is number => n !== null).sort((a, b) => a - b);
  const cut = sorted[Math.max(0, Math.ceil(sorted.length * .9) - 1)] ?? Infinity;
  const sumWeights = f.weights.reduce((a, b) => a + b, 0) || 100;
  return entries.map((r, index) => {
    const key = `${r.id}:${f.day}`;
    const bandFactor = { all: .8, am: 1.4, midday: .55, pm: 1.3, evening: .4 }[f.period];
    const passengers = round((.8 + 30 * sample(`${key}:load`) ** 2) * bandFactor);
    const trip = round(r.station_overlap * (.3 + .7 * sample(`${key}:trip`)) * (f.period === 'am' || f.period === 'pm' ? .85 : 1));
    const coldShare = round(clamp(sample(`${key}:cold`) * (1.2 - bandFactor * .4)), 3);
    const uninterrupted = Math.round(45 + sample(`${key}:${f.period}:driving`) * 65);
    const rest = Math.round(3 + sample(`${key}:rest`) * 22);
    const series = r.hourly[f.day];
    const peak = series ? ((mean(series, 7, 9) ?? 0) + (mean(series, 17, 19) ?? 0)) / 2 : null;
    const frequency = freq[index];
    const stack = Math.max(clamp(r.shared_routes / 30), frequency !== null && cut > 0 ? clamp(frequency / cut) : 0);
    const factors = [
      .75 * clamp(trip / 40) + .25 * clamp(r.station_overlap / f.overlap),
      stack, clamp(1 - passengers / (f.passengers * 2)), coldShare,
    ];
    const components = factors.map((v, i) => round(v * f.weights[i] * 100 / sumWeights));
    const problems: Ranked['problems'] = [];
    if (trip >= 40 || r.station_overlap >= f.overlap) problems.push('metro');
    if (r.shared_routes >= 15 || (frequency !== null && frequency > 0 && frequency >= cut)) problems.push('stack');
    if (passengers < f.passengers || coldShare >= .5) problems.push('cold');
    if (uninterrupted > 75) problems.push('rest');
    const recommendation = r.kind === 'terminals'
      ? (uninterrupted > 75 ? '調整班表，補足端點休息時間' : '核對停靠與如廁可用時段')
      : r.kind === 'drts' ? (problems.includes('cold') ? '評估需求反應式預約服務' : '保留定時班次，觀察需求')
      : problems.includes('metro') && problems.includes('stack') ? '評估平行路段減班與轉乘銜接'
      : problems.includes('cold') ? (r.shared_routes < 5 ? '評估 DRTS 或小型巴士服務' : '檢視離峰班次與共線整併')
      : problems.includes('metro') ? '檢視捷運平行段，保留非重疊服務'
      : problems.includes('stack') ? '整合共站路線，協調發車間隔'
      : problems.includes('rest') ? '調整班表，補足休息時間' : '持續觀察，維持現有服務';
    return { ...r, is_mock: true, frequency, peak, offpeak: mean(series, 9, 17), partial: r.missing_schedules[f.day] > 0,
      passengers, trip, coldShare, rest, uninterrupted, components, score: Math.round(components.reduce((a, b) => a + b, 0)),
      coldPeriod: coldShare >= .5 || passengers < f.passengers ? (f.period === 'all' ? '09–17 時' : PERIODS[f.period].label) : '—',
      problems, recommendation, affectedTrips: Math.round(200 + sample(`${key}:affected`) * 2200),
      releasedHours: Math.round(15 + sample(`${key}:hours`) * 150), populationChange: -round(sample(`${key}:population`) * 2),
    };
  });
}

export function matches(r: Ranked, f: Pick<Filters, 'district' | 'query' | 'problems'>): boolean {
  const query = f.query.trim().toLocaleLowerCase();
  return (!f.district || r.districts.includes(f.district))
    && (!query || `${r.name} ${r.subtitle ?? ''} ${r.source_id} ${r.districts.join(' ')}`.toLocaleLowerCase().includes(query))
    && (!f.problems.length || f.problems.some((p) => r.problems.includes(p)));
}

/** Explain the first useful recovery action when no visible result remains. */
export function emptyResults(f: Filters, keywordMatches: boolean, filteredCount: number) {
  if (f.query.trim() && !keywordMatches) return {
    title: `沒有符合關鍵字的${KINDS[f.kind]}`,
    hint: '請確認名稱或編號，或清除搜尋關鍵字。', showLow: false,
  };
  if (!filteredCount) return {
    title: `沒有符合目前篩選的${KINDS[f.kind]}`,
    hint: '試著變更行政區、問題篩選，或重設為預設值。', showLow: false,
  };
  return {
    title: `符合條件的${KINDS[f.kind]}皆為低優先項目`,
    hint: '可展開低優先項目（<60 分），或調整評分偏好與門檻。', showLow: true,
  };
}
export function sortValue(r: Ranked, key: SortKey): string | number | null {
  switch (key) {
    case 'geometry': return r.geometry_overlap;
    case 'station': return r.station_overlap;
    case 'shared': return r.shared_routes;
    case 'cold': return r.coldShare;
    case 'mrt': return r.nearest_mrt?.distance_m ?? null;
    case 'toilet': return r.nearest_toilet?.distance_m ?? null;
    case 'population': return r.population ?? null;
    case 'routes': return r.route_ids.length;
    case 'problems': return r.problems.map((p) => PROBLEMS[p]).join('、');
    default: return r[key];
  }
}
export function sortRows(rows: Ranked[], f: Pick<Filters, 'sort' | 'descending'>): Ranked[] {
  return [...rows].sort((a, b) => {
    const av = sortValue(a, f.sort), bv = sortValue(b, f.sort);
    if (av === null || bv === null) return av === bv ? a.id.localeCompare(b.id) : av === null ? 1 : -1;
    const compare = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv), 'zh-Hant', { numeric: true });
    return compare * (f.descending ? -1 : 1) || a.id.localeCompare(b.id);
  });
}

export function loadFilters(hash: string): Filters {
  try {
    const raw = new URLSearchParams(hash.replace(/^#/, '')).get('improvement');
    if (!raw) return { ...DEFAULTS };
    const v = JSON.parse(raw);
    const numeric = (key: string, min: number, max: number, fallback: number) => typeof v[key] === 'number' && Number.isFinite(v[key]) ? clamp(Math.round(v[key]), min, max) : fallback;
    const sorts: SortKey[] = ['name', 'score', 'problems', 'geometry', 'station', 'trip', 'shared', 'frequency', 'offpeak', 'passengers', 'cold', 'recommendation', 'mrt', 'population', 'routes', 'toilet', 'rest', 'uninterrupted'];
    return { ...DEFAULTS, kind: Object.hasOwn(KINDS, v.kind) ? v.kind : DEFAULTS.kind,
      day: ['weekday', 'saturday', 'sunday'].includes(v.day) ? v.day : DEFAULTS.day,
      period: Object.hasOwn(PERIODS, v.period) ? v.period : DEFAULTS.period,
      district: typeof v.district === 'string' ? v.district.slice(0, 80) : '', query: typeof v.query === 'string' ? v.query.slice(0, 100) : '',
      overlap: numeric('overlap', 10, 100, 70), passengers: numeric('passengers', 1, 30, 5),
      problems: Array.isArray(v.problems) ? [...new Set(v.problems.filter((p: string) => Object.hasOwn(PROBLEMS, p)))] as Filters['problems'] : [],
      weights: Array.isArray(v.weights) && v.weights.length === 4 && v.weights.every((w: unknown) => typeof w === 'number' && Number.isFinite(w) && w >= 0 && w <= 100) && v.weights.some((w: number) => w > 0) ? v.weights : DEFAULTS.weights,
      sort: sorts.includes(v.sort) ? v.sort : 'score', descending: v.descending !== false,
      showLow: v.showLow === true, view: v.view === 'map' ? 'map' : 'list', selected: typeof v.selected === 'string' ? v.selected.slice(0, 120) : null,
    };
  } catch { return { ...DEFAULTS }; }
}
export function saveFilters(f: Filters): void {
  const params = new URLSearchParams(window.location.hash.slice(1));
  params.set('improvement', JSON.stringify(f));
  window.history.replaceState(null, '', `#${params}`);
}

export function csv(rows: Ranked[], f: Filters, sourceTime: string): string {
  const headers = ['id', 'category', 'name', 'districts', 'day', 'period', 'overlap_threshold', 'passenger_threshold', 'priority_score', 'priority_score_is_mock',
    'problems', 'problems_is_mock', 'geometry_overlap_pct', 'geometry_overlap_is_mock', 'station_overlap_pct', 'station_overlap_is_mock',
    'trip_overlap_pct', 'trip_overlap_is_mock', 'shared_routes', 'shared_routes_is_mock', 'planned_frequency', 'peak_frequency', 'offpeak_frequency', 'frequency_is_mock', 'missing_schedules',
    'passengers_per_trip', 'passengers_per_trip_is_mock', 'cold_period', 'cold_share', 'cold_is_mock', 'nearest_mrt', 'mrt_distance_m', 'mrt_is_mock',
    'route_count', 'stop_count', 'population', 'population_missing_cells', 'population_is_mock', 'nearest_toilet', 'toilet_distance_m', 'toilet_is_mock',
    'rest_minutes', 'uninterrupted_minutes', 'rest_is_mock', 'affected_trips', 'released_vehicle_hours', 'population_change_pct', 'impact_is_mock',
    'recommendation', 'recommendation_is_mock', 'is_mock', 'weights', 'source_data_time'];
  const cell = (v: unknown) => {
    let s = v === null || v === undefined ? '' : String(v);
    if (typeof v === 'string' && /^[\s]*[=+@-]/.test(s)) s = `'${s}`;
    return `"${s.replaceAll('"', '""')}"`;
  };
  const records = rows.map((r) => [r.id, KINDS[r.kind], r.name, r.districts.join('、'), f.day, f.period, f.overlap, f.passengers, r.score, 1,
    r.problems.map((p) => PROBLEMS[p]).join('、'), 1, r.geometry_overlap, 0, r.station_overlap, 0, r.trip, 1, r.shared_routes, 0,
    r.frequency, r.peak, r.offpeak, 0, r.missing_schedules[f.day], r.passengers, 1, r.coldPeriod, r.coldShare, 1,
    r.nearest_mrt?.name, r.nearest_mrt?.distance_m, 0, r.route_ids.length, r.stop_ids.length, r.population, r.population_missing_cells, 0,
    r.nearest_toilet?.name, r.nearest_toilet?.distance_m, 0, r.rest, r.uninterrupted, 1, r.affectedTrips, r.releasedHours, r.populationChange, 1,
    r.recommendation, 1, 1, f.weights.join('/'), sourceTime]);
  return '\uFEFF' + [headers, ...records].map((row) => row.map(cell).join(',')).join('\r\n');
}
