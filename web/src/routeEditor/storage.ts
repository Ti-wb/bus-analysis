import type { AppliedAction, Changes, Route, ScenarioData } from './types';
import { BANDS } from './types';

const KEY = 'bus-coming-soon:route-scenario:v1';
export const SCENARIO_HISTORY_LIMIT = 5;
export function loadScenario(data: ScenarioData): { changes: Changes; history: AppliedAction[]; message: string } {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { changes: {}, history: [], message: '' };
    const saved = JSON.parse(raw);
    if (saved.source !== data.metadata.source_data_time || ![1, 2].includes(saved.version)) return { changes: {}, history: [], message: '基準資料已更新；本次使用新基準，舊方案未載入。' };
    const known = new Set(data.records.routes.map((r) => r.id));
    const validRoute = (r: Route) => r && typeof r.id === 'string' && typeof r.route_id === 'string'
      && ['subroute_id', 'branch_name', 'direction', 'direction_label'].every((key) => typeof r[key as keyof Route] === 'string')
      && typeof r.name === 'string' && r.name.trim().length > 0 && r.name.length <= 100
      && (known.has(r.id) || r.id.startsWith('custom:')) && Array.isArray(r.stop_ids) && r.stop_ids.length <= 300
      && new Set(r.stop_ids).size >= 2 && r.stop_ids.every((id) => typeof id === 'string' && Object.hasOwn(data.records.stops, id))
      && (r.path === null || (Array.isArray(r.path) && r.path.length >= 2 && r.path.length <= 3000
        && r.path.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 90)))
      && r.hourly && ['weekday', 'saturday', 'sunday'].every((d) => {
        const h = r.hourly[d as keyof Route['hourly']];
        return h === null || (Array.isArray(h) && h.length === 24 && h.every((n) => Number.isFinite(n) && n >= 0));
      }) && (r.frequencies === undefined || (r.frequencies !== null && typeof r.frequencies === 'object' && !Array.isArray(r.frequencies)
        && Object.entries(r.frequencies).every(([b, f]) => BANDS.some((v) => v.id === b) && Number.isFinite(f) && f >= 0 && f <= 60)));
    const validChanges = (changes: Changes) => changes && typeof changes === 'object' && !Array.isArray(changes)
      && Object.entries(changes).length <= 2000 && Object.entries(changes).every(([id, r]) =>
        (known.has(id) || id.startsWith('custom:')) && (r === null || (r.id === id && validRoute(r))));
    if (!Array.isArray(saved.history) || saved.history.length > 20) throw new Error('invalid');
    // Version 1 remains readable; version 2 shares route snapshots across undo states.
    if (saved.version === 2) {
      if (!Array.isArray(saved.routes) || !saved.routes.every(validRoute)) throw new Error('invalid');
      const route = (index: number): Route => {
        if (!Number.isInteger(index) || index < 0 || index >= saved.routes.length) throw new Error('invalid');
        return saved.routes[index];
      };
      const expand = (refs: Record<string, number | null>): Changes => {
        if (!refs || typeof refs !== 'object' || Array.isArray(refs) || Object.keys(refs).length > 2000) throw new Error('invalid');
        return Object.fromEntries(Object.entries(refs).map(([id, index]) => [id, index === null ? null : route(index)]));
      };
      saved.changes = expand(saved.changes);
      saved.history = saved.history.map((a: StoredAction) => ({ ...a, route: route(a.route), original: a.original === null ? null : route(a.original), before: expand(a.before), after: expand(a.after) }));
    }
    if (!validChanges(saved.changes) || !saved.history.every((a: AppliedAction) =>
      ['add', 'edit', 'delete'].includes(a.kind) && BANDS.some((b) => b.id === a.band)
      && Number.isFinite(a.speed) && a.speed >= 5 && a.speed <= 60 && Number.isFinite(a.dwell) && a.dwell >= 0 && a.dwell <= 180
      && validRoute(a.route) && (a.original === null || validRoute(a.original)) && validChanges(a.before) && validChanges(a.after))) throw new Error('invalid');
    return { changes: saved.changes, history: saved.history.slice(-SCENARIO_HISTORY_LIMIT), message: Object.keys(saved.changes).length ? '已還原此裝置的模擬方案。' : '' };
  } catch { return { changes: {}, history: [], message: '本機方案無法讀取；已開啟原始路網，可重新建立方案。' }; }
}

type StoredChanges = Record<string, number | null>;
type StoredAction = Omit<AppliedAction, 'route' | 'original' | 'before' | 'after'> & {
  route: number; original: number | null; before: StoredChanges; after: StoredChanges;
};

export function saveScenario(data: ScenarioData, changes: Changes, history: AppliedAction[]): boolean {
  try {
    const routes: Route[] = [], indices = new Map<string, number>();
    const route = (value: Route) => {
      const key = JSON.stringify(value);
      let index = indices.get(key);
      if (index === undefined) { index = routes.length; routes.push(value); indices.set(key, index); }
      return index;
    };
    const compact = (values: Changes): StoredChanges => Object.fromEntries(Object.entries(values).map(([id, value]) => [id, value === null ? null : route(value)]));
    const savedChanges = compact(changes);
    const savedHistory: StoredAction[] = history.slice(-SCENARIO_HISTORY_LIMIT).map((a) => ({ ...a, route: route(a.route), original: a.original === null ? null : route(a.original), before: compact(a.before), after: compact(a.after) }));
    localStorage.setItem(KEY, JSON.stringify({ version: 2, source: data.metadata.source_data_time, routes, changes: savedChanges, history: savedHistory }));
    return true;
  } catch { return false; }
}
