import { useMemo } from 'react';
import { nearby } from '../catchment';
import { StationIcon } from '../components/StationInfo';
import { COVERAGE_ALPHA, COVERAGE_LAYERS, COVERAGE_OPACITY } from '../config';
import { STATION_RADIUS } from '../stations';
import type { MapData } from '../types';
import NearbyMap from './NearbyMap';
import type { BusFocusState, BusStation } from './model';
import './focus.css';

function Eye({ visible }: { visible: boolean }) {
  return <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
    <path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z" /><circle cx="10" cy="10" r="2.4" />
    {!visible && <path d="m3 3 14 14" strokeWidth="1.8" />}
  </svg>;
}

interface Props {
  station: BusStation;
  data: MapData;
  focus: BusFocusState;
  onClose: () => void;
  onOverview: (scope: 'local' | 'routes') => void;
  onSelectRoute: (routeId: string) => void;
  onToggleRoute: (routeId: string) => void;
  onHoverRoute: (routeId: string | null) => void;
  onShowAll: () => void;
}

export default function FocusSidebar({ station, data, focus, onClose, onOverview, onSelectRoute, onToggleRoute, onHoverRoute, onShowAll }: Props) {
  const neighbours = useMemo(() => nearby({ ...data, regional_bus_stops: null }, station.at, STATION_RADIUS, station.id, new Set()), [data, station]);
  const visible = focus.routes.filter((route) => route.visible && route.has_geometry).length;
  const enabled = focus.routes.filter((route) => route.visible).length;
  const available = focus.routes.filter((route) => route.has_geometry).length;
  const total = neighbours.stops + 1 + neighbours.mrt.count + neighbours.youbike.count;
  const allView = focus.camera.scope === 'routes' && focus.selected_route_id === null;

  return <aside className="focus-sidebar" aria-label="公車站點 Focus">
    <header className="focus-header">
      <button className="focus-back" onClick={onClose}><span aria-hidden="true">←</span> 返回探索</button>
      <div className="focus-station-kind"><StationIcon type="bus" /><span>公車站</span><span className="focus-tag">站點 Focus</span></div>
      <h1>{station.properties.name}</h1>
      <p>周邊 350 m ＋ 本站服務路線</p>
    </header>

    <section className="focus-nearby" aria-labelledby="focus-nearby-title">
      <div className="focus-section-heading"><h2 id="focus-nearby-title">周邊 350 m</h2><span>{total} 站・含本站</span></div>
      <p className="focus-nearby-counts"><span><i className="focus-dot-bus" />公車 {neighbours.stops + 1}</span><span><i className="focus-dot-mrt" />捷運 {neighbours.mrt.count}</span><span><i className="focus-dot-bike" />YouBike {neighbours.youbike.count}</span></p>
      <NearbyMap data={data} station={station} routes={focus.routes} onLocate={() => onOverview('local')} />
      <p className="focus-caption">僅納入本站周邊，不從近鄰再延伸</p>
    </section>

    <section className="focus-route-section" aria-labelledby="focus-routes-title">
      <div className="focus-section-heading"><h2 id="focus-routes-title">本站服務路線</h2><span>全部 {focus.routes.length} 條</span></div>
      <div className="focus-view-switch" aria-label="地圖視野">
        <button className={allView ? 'is-active' : ''} aria-pressed={allView} onClick={() => onOverview('routes')}><svg viewBox="0 0 18 18" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M6 2H2v4m10-4h4v4M2 12v4h4m10-4v4h-4M5 9h8M9 5v8" /></svg>路線總覽</button>
        <button className={focus.camera.scope === 'local' ? 'is-active' : ''} aria-pressed={focus.camera.scope === 'local'} onClick={() => onOverview('local')}>350 m 周邊</button>
      </div>
      {focus.routes.length > 0 ? <>
        <p className="focus-route-hint">點路線定位，眼睛切換路線、停靠站與覆蓋</p>
        <ul className="focus-routes">
          {focus.routes.map((route) => <li key={route.route_id} className={`${focus.selected_route_id === route.route_id ? 'is-selected' : ''}${!route.visible ? ' is-hidden' : ''}`}>
            <button className="focus-route-name" disabled={!route.has_geometry}
              aria-label={`在地圖上檢視 ${route.name} 路線${!route.has_geometry ? '，無線形資料' : ''}`}
              aria-pressed={focus.selected_route_id === route.route_id}
              onClick={() => onSelectRoute(route.route_id)}
              onMouseEnter={() => route.visible && onHoverRoute(route.route_id)} onMouseLeave={() => onHoverRoute(null)}
              onFocus={() => route.visible && onHoverRoute(route.route_id)} onBlur={() => onHoverRoute(null)}>
              <i className="focus-route-color" style={{ background: route.color }} />
              <span><b>{route.name}</b><small>{route.has_geometry ? route.weekday_departures === null ? '班次未提供' : `平日計畫 ${route.weekday_departures.toLocaleString('zh-TW')} 班` : '尚無路線線形'}</small></span>
            </button>
            <button className="focus-route-visibility" aria-label={`${route.visible ? '隱藏' : '顯示'} ${route.name} 路線`} aria-pressed={route.visible} onClick={() => onToggleRoute(route.route_id)}><Eye visible={route.visible} /></button>
          </li>)}
        </ul>
        <div className="focus-route-state" role="status"><span>已開啟 {enabled} / {focus.routes.length} 條・{visible} 條有線形</span>{enabled < focus.routes.length && <button onClick={onShowAll}>全部顯示</button>}</div>
        {enabled === 0 && <p className="focus-no-routes">服務路線已隱藏，覆蓋僅保留本站 350 m。按「全部顯示」恢復路線。</p>}
        {available < focus.routes.length && <p className="focus-no-routes">{focus.routes.length - available} 條路線缺少線形，仍可切換已知停靠站與覆蓋。</p>}
      </> : <p className="focus-no-routes">本站尚無可連接的服務路線資料，先查看 350 m 周邊。</p>}
    </section>

    <footer className="focus-footer">
      <div><i className="focus-legend-coverage" style={{ background: `linear-gradient(90deg, ${COVERAGE_ALPHA.map((alpha) => `rgba(${COVERAGE_LAYERS.walk.rgb.join(',')},${alpha * COVERAGE_OPACITY})`).join(',')})` }} />沿線公車站 350 m 直線覆蓋</div>
      <div><i className="focus-legend-circle" />本站 350 m 直線範圍</div>
      <div><i className="focus-legend-route" />其他路線暫時淡化</div>
      <p>橙色越深，距已開啟路線的公車站越近。班次為全日計畫發車，非本站到站時刻；周邊站數依來源站位計算。</p>
    </footer>
  </aside>;
}
