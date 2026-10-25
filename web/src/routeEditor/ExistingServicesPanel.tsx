import { useMemo } from 'react';
import type { ExistingService } from './existingServices';
import { rate } from './Impact';
import { BANDS, type Band, type Draft, type ScenarioData } from './types';

interface Props {
  data: ScenarioData;
  services: ExistingService[];
  selectedStopIds: string[];
  band: Band;
  draft: Draft;
  draftFrequency: number | null;
  applied: boolean;
  highlightedRouteId: string | null;
  onHighlight: (routeId: string | null) => void;
}

export default function ExistingServicesPanel({ data, services, selectedStopIds, band, draft, draftFrequency, applied, highlightedRouteId, onHighlight }: Props) {
  const selectedAreas = useMemo(() => {
    const areas = new Map<string, number[]>();
    selectedStopIds.forEach((id, index) => areas.set(id, [...(areas.get(id) ?? []), index + 1]));
    return areas;
  }, [selectedStopIds]);
  const groups = useMemo(() => {
    const grouped = new Map<string, ExistingService[]>();
    for (const service of services) {
      const id = service.route.route_id;
      grouped.set(id, [...(grouped.get(id) ?? []), service]);
    }
    return [...grouped.entries()].map(([id, variants]) => {
      const known = variants.flatMap((variant) => variant.frequency === null ? [] : [variant.frequency]);
      const missing = known.length < variants.length;
      const min = Math.min(...known), max = Math.max(...known);
      const range = rate(min) === rate(max) ? rate(min) : `${rate(min)}–${rate(max)}`;
      const summary = !known.length ? '班表未提供'
        : max === 0 && !missing ? '本時段 0 班'
          : `${missing ? '已知方向' : '各附線方向'} ${range} 班/h`;
      return {
        id, name: variants[0].route.name, variants, summary,
        frequencyState: !known.length ? 'is-unknown' : max === 0 ? 'is-zero' : '',
        partiallyMissing: known.length > 0 && missing,
        areaCount: new Set(variants.flatMap((variant) => variant.areaMatches.map((area) => area.selectedStopId))).size,
      };
    });
  }, [services]);
  const areaLabel = (id: string) => `第 ${selectedAreas.get(id)?.join('、') ?? '—'} 站・${data.records.stops[id]?.name ?? id}`;

  return <aside className="card route-existing-services" aria-labelledby="route-existing-services-title">
    <header className="route-services-head">
      <p className="route-eyebrow">新增路線・周邊參考</p>
      <div className="route-services-title"><h2 id="route-existing-services-title">站區既有服務</h2>{applied && <span className="route-tag">含已套用方案</span>}</div>
      <p className="route-services-scope">已選站周邊直線 350 m</p>
      <p className="route-services-band"><b>{BANDS.find((item) => item.id === band)?.label}</b><span>時段平均班次 · 班/h</span></p>
      <div className="route-services-count"><b aria-live="polite">{groups.length} 條路線<span>／{selectedAreas.size} 個已選站區</span></b>
        {highlightedRouteId !== null && <button className="link-btn" onClick={() => onHighlight(null)}>收起參考線</button>}</div>
      <p className="route-note">點選一條路線，才會顯示參考線；收合後回到草稿。</p>
      <div className="route-services-key" aria-label="既有服務地圖圖例"><span><i />既有路線</span><span><i className="is-zero" />本時段 0 班</span><span><i className="is-unknown" />班表未提供</span></div>
      <p className="route-services-assumption route-note">班表未分方向，同一附線各方向沿用相同班表。</p>
    </header>
    <div className="route-services-body">
      {!selectedAreas.size ? <div className="route-services-empty" role="status"><b>加入站點，即可查看周邊服務</b><p>從地圖或左側搜尋加入第一站，這裡會列出停靠 350 m 內公車站的既有路線。</p></div>
        : !groups.length ? <div className="route-services-empty" role="status"><b>已選站區沒有符合的既有路線</b><p>目前路線資料中，未找到停靠這些站區內公車站的路線。</p></div>
          : <ul className="route-service-groups" aria-label="站區既有路線">
            {groups.map((group) => <li key={group.id} className={`route-service-group${highlightedRouteId === group.id ? ' is-highlighted' : ''}`}>
              <button className="route-service-select" aria-pressed={highlightedRouteId === group.id} aria-expanded={highlightedRouteId === group.id}
                aria-controls={highlightedRouteId === group.id ? `route-service-details-${group.id}` : undefined}
                onClick={() => onHighlight(highlightedRouteId === group.id ? null : group.id)}>
                <b>{group.name}</b><span className="route-service-select-state">{highlightedRouteId === group.id ? '收合 ↑' : '展開 ↓'}</span>
                <small className="route-service-meta">{group.variants.length} 個附線方向 · 涵蓋 {group.areaCount} 個已選站區</small>
                <span className={`route-service-summary ${group.frequencyState}`}>{group.summary}{group.partiallyMissing && <small>部分班表未提供</small>}</span>
              </button>
              {highlightedRouteId === group.id && <ul className="route-service-variants" id={`route-service-details-${group.id}`}>
                {group.variants.map((service) => <li key={service.route.id}>
                  <div className="route-service-variant-head"><b>{service.route.branch_name || service.route.name}<span> · {service.route.direction_label}</span></b>
                    <span className={`route-service-frequency${service.frequency === null ? ' is-unknown' : service.frequency === 0 ? ' is-zero' : ''}`}>
                      {service.frequency === null ? '班表未提供' : service.frequency === 0 ? '本時段 0 班' : <>{rate(service.frequency)} <small>班/h</small></>}
                    </span></div>
                  <ul className="route-service-areas">
                    {service.areaMatches.map((area) => <li key={area.selectedStopId}>
                      <span>{areaLabel(area.selectedStopId)}</span><em className={area.direct ? 'is-direct' : ''}>{area.direct ? '停靠本站' : '周邊站可搭'}</em>
                      {!area.direct && <small>搭乘站：{area.boardingStopIds.map((id) => `${data.records.stops[id]?.name ?? id}（#${id}）`).join('、')}</small>}
                    </li>)}
                  </ul>
                </li>)}
              </ul>}
            </li>)}
          </ul>}
      {!!groups.length && <p className="route-services-boundary route-note">各方向班次分列，不合計去返程；站區命中不判定直達或轉乘。</p>}
    </div>
    <footer className="route-services-draft">
      <div><span className="route-tag">草稿</span><b>{draft.route.name || '未命名路線'}</b></div>
      <p>{draft.route.direction_label} · {selectedStopIds.length} 個停靠站 · 每小時 {rate(draftFrequency)} 班</p>
      <p className="route-note">人口與設施影響請至下一步「預覽影響」比較。</p>
    </footer>
  </aside>;
}
