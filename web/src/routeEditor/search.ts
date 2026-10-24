import type { Route } from './types';

export interface RouteMatch { routes: Route[]; reason: string | null }

/** Search is a view of the list; it never changes the routes used for assessment. */
export function searchRouteGroups(groups: Route[][], input: string): RouteMatch[] {
  const query = input.trim().toLocaleLowerCase();
  if (!query) return groups.map((routes) => ({ routes, reason: null }));
  return groups.flatMap((routes) => {
    const fields = routes.flatMap((r) => [
      { value: r.name, label: '名稱', priority: 0 },
      { value: r.branch_name, label: '附線名稱', priority: 2 },
      { value: r.route_id, label: '來源 ID', priority: 4 },
    ]).filter((field) => field.value.toLocaleLowerCase().includes(query))
      .map((field) => ({ ...field, priority: field.priority + Number(field.value.toLocaleLowerCase() !== query) }))
      .sort((a, b) => a.priority - b.priority);
    const match = fields[0];
    return match ? [{ routes, reason: `符合${match.label}：${match.value}`, priority: match.priority }] : [];
  }).sort((a, b) => a.priority - b.priority);
}
