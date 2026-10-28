import type { AppliedAction, Changes, Route, ScenarioData } from './types';
import { BANDS } from './types';

const KEY = 'bus-coming-soon:route-scenario:v1';
export function loadScenario(data: ScenarioData): { changes: Changes; history: AppliedAction[]; message: string } {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { changes: {}, history: [], message: '' };
    const saved = JSON.parse(raw);
    if (saved.source !== data.metadata.source_data_time || saved.version !== 1) return { changes: {}, history: [], message: '基準資料已更新；本次使用新基準，舊方案未載入。' };
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
    if (!validChanges(saved.changes) || !Array.isArray(saved.history) || saved.history.length > 20 || !saved.history.every((a: AppliedAction) =>
      ['add', 'edit', 'delete'].includes(a.kind) && BANDS.some((b) => b.id === a.band)
      && Number.isFinite(a.speed) && a.speed >= 5 && a.speed <= 60 && Number.isFinite(a.dwell) && a.dwell >= 0 && a.dwell <= 180
      && validRoute(a.route) && (a.original === null || validRoute(a.original)) && validChanges(a.before) && validChanges(a.after))) throw new Error('invalid');
    return { changes: saved.changes, history: saved.history, message: Object.keys(saved.changes).length ? '已還原此裝置的模擬方案。' : '' };
  } catch { return { changes: {}, history: [], message: '本機方案無法讀取；已開啟原始路網，可重新建立方案。' }; }
}

export function saveScenario(data: ScenarioData, changes: Changes, history: AppliedAction[]): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify({ version: 1, source: data.metadata.source_data_time, changes, history }));
    return true;
  } catch { return false; }
}
