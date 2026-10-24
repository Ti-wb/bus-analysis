import { useEffect, useRef } from 'react';
import { AGE_COLORS, AGE_UNKNOWN, groupFor, isVerifiedVillage, joinStatusText, percentText, villageMeasure,
  type AgeGroup, type AgeMetadata, type VillageAgeProps, type VillageAges } from '../age';
import type { AppState } from '../types';

export function AgeControls({ state, data, error, onChange, onRetry, selectedVillage, onVillage }: {
  state: AppState; data: VillageAges | null; error: Error | null;
  onChange: (patch: Partial<AppState>) => void; onRetry: () => void;
  selectedVillage: string | null; onVillage: (id: string | null) => void;
}) {
  const metadata = data?.metadata;
  const groups = metadata?.age_groups.filter((group) => group.set === state.ageSet) ?? [];
  const current = metadata && groupFor(metadata, state.ageGroup, state.ageSet);
  const national = metadata?.national_audit_summary;
  return <div className="age-controls" aria-busy={!data && !error}>
    <h2>村里年齡</h2>
    <p className="age-scope">全國清冊核對 · 北北基既有地圖範圍</p>
    {error ? <div role="alert"><p>年齡圖層載入失敗。</p><button className="station-text-button" onClick={onRetry}>重新載入年齡圖層</button></div>
      : !metadata ? <p role="status">年齡圖層載入中…</p>
        : <>
          <label className="age-label">年齡分組<select aria-label="年齡分組" value={state.ageSet} onChange={(event) => {
            const ageSet = event.target.value as AppState['ageSet'];
            const first = metadata.age_groups.find((group) => group.set === ageSet);
            onChange({ ageSet, ageGroup: first?.key ?? '' });
          }}><option value="three_stage">三段年齡</option><option value="planning">細分年齡</option></select></label>
          <label className="age-label">地圖顯示的年齡層<select aria-label="地圖顯示的年齡層" value={current?.key} onChange={(event) => onChange({ ageGroup: event.target.value })}>
            {groups.map((group) => <option key={group.key} value={group.key}>{group.label}</option>)}
          </select></label>
          <label className="age-label">查詢村里<select aria-label="查詢村里" value={selectedVillage ?? ''} onChange={(event) => onVillage(event.target.value || null)}>
            <option value="">選擇村里，或點選地圖</option>
            {data!.features.map(({ properties: p }) => <option key={p.boundary_id} value={p.boundary_id}>{p.county_name}{p.district_name}{p.boundary_village_name || p.village_name || '未編定村里範圍'} · {p.boundary_id}</option>)}
          </select></label>
          <p className="muted age-source-line">人口 {metadata.population_period} · 里界 {metadata.boundary_vintage}</p>
          {!metadata.same_month_geometry_verified && <p className="age-warning">里界與人口月份不同；尚未證明同月幾何一致。</p>}
          {national && <details className="age-details age-audit"><summary>全國核對與地圖範圍</summary>
            <p>人口 {national.population_village_count.toLocaleString('zh-TW')} 里／清冊 {national.roster_village_count.toLocaleString('zh-TW')} 里；里界 {national.boundary_feature_count.toLocaleString('zh-TW')} 圖形，包含非村里範圍。</p>
            <p>全國有 {national.excluded_population_village_count} 個人口村里未通過里界核對（來源已知人口 {national.excluded_source_population_known_sum.toLocaleString('zh-TW')} 人），不納入面積分攤。名稱不符 {national.boundary_status_counts.name_mismatch ?? 0} 里、幾何衝突 {national.boundary_status_counts.geometry_conflict ?? 0} 里。</p>
            <p>本圖載入 {data!.features.length.toLocaleString('zh-TW')} 個北北基里界圖形（部分超出既有視野），其中 {data!.features.filter((feature) => !isVerifiedVillage(feature.properties.boundary_join_status)).length} 個未匹配；未匹配及缺值均保留為未知。</p>
          </details>}
          <p className="muted age-help">方向鍵移動地圖，Enter 查詢畫面中央的村里；點選站點可看 350 m 年齡估算。</p>
        </>}
  </div>;
}

export function AgeLegend({ data, groupKey, ageSet }: { data: VillageAges | null; groupKey: string; ageSet: AppState['ageSet'] }) {
  const group = data && groupFor(data.metadata, groupKey, ageSet);
  return <section className="card legend age-legend">
    <h3>{group?.label ?? '選定年齡層'}占村里戶籍人口比例</h3>
    <div className="legend-scale legend-scale-5">{AGE_COLORS.map((color) => <span key={color} style={{ background: color }} />)}</div>
    <div className="age-legend-labels"><span>0–10%</span><span>10–20%</span><span>20–30%</span><span>30–40%</span><span>40%+</span></div>
    <p className="legend-note"><i style={{ background: AGE_UNKNOWN }} />灰色＝未匹配、缺值，或總人口為 0 而比例無分母</p>
    <p className="legend-note">整個村里的戶籍結構；非站圈估算或實際乘車需求。</p>
  </section>;
}

export function VillageFacts({ properties: p, metadata, group, compact = false }: {
  properties: VillageAgeProps; metadata: AgeMetadata; group: AgeGroup; compact?: boolean;
}) {
  const value = villageMeasure(p, group);
  return <>
    <b>{p.county_name}{p.district_name}{p.village_name || p.boundary_village_name || '未編定村里範圍'}</b>
    <p>{group.label}：{value.count === null ? '資料未知' : `${value.count.toLocaleString('zh-TW')} 人`}
      {value.share !== null && `（${percentText(value.share)}）`}</p>
    {isVerifiedVillage(p.boundary_join_status) && p.population === 0 && <p>總人口 0 人，比例無分母。</p>}
    <p>{joinStatusText(p.boundary_join_status)}</p>
    <p>人口／清冊 {metadata.population_period}／{metadata.roster_period}</p>
    <p>里界版本 {metadata.boundary_vintage}</p>
    {!metadata.same_month_geometry_verified && <p className={compact ? 'warn' : 'age-warning'}>尚未證明人口月與里界幾何同月一致。</p>}
    {!compact && <>
      <p>人口碼：{isVerifiedVillage(p.boundary_join_status) ? p.area_id : '無可對應人口碼'} · 里界碼 {p.boundary_id}</p>
      {p.boundary_village_name !== p.village_name && <p>里界名稱：{p.boundary_village_name ?? '未提供'}</p>}
      <p>全村戶籍總人口：{p.population === null ? '未知' : `${p.population.toLocaleString('zh-TW')} 人`}。未匹配村里不計比例。</p>
      <details className="age-details"><summary>核對與來源限制</summary><ul>{metadata.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></details>
      <p className="age-source-line">資料來源：<a href="https://data.gov.tw/dataset/77132" target="_blank" rel="noreferrer">內政部戶政司</a>、內政部國土測繪中心。<a href="https://data.gov.tw/license" target="_blank" rel="noreferrer">政府資料開放授權條款第 1 版</a>。</p>
      <p className="age-warning">戶籍人口背景，不能視為實際乘車需求。</p>
    </>}
  </>;
}

export function VillageDialog({ properties, metadata, group, onClose }: {
  properties: VillageAgeProps; metadata: AgeMetadata; group: AgeGroup; onClose: () => void;
}) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { close.current?.focus({ preventScroll: true }); }, [properties.boundary_id]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && !(event.target as HTMLElement).closest('dialog, [role="dialog"][aria-modal="true"]')) { event.preventDefault(); onClose(); }
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [onClose]);
  return <section className="card village-dialog" role="dialog" aria-modal="false" aria-labelledby="village-age-title">
    <header><h2 id="village-age-title">村里年齡資料</h2><button ref={close} className="station-close" onClick={onClose} aria-label="關閉村里年齡資訊">×</button></header>
    <VillageFacts properties={properties} metadata={metadata} group={group} />
  </section>;
}
