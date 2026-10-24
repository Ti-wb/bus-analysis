import { useEffect, useState } from 'react';
import { catchmentGroupValue, estimateText, percentText, type AgeCatchment, type AgeGroup, type StationAgeCatchments } from '../age';
import { loadStationAges } from '../data';
import { STATION_RADIUS } from '../stations';
import type { StationSelection } from '../types';

export default function StationAge({ station }: { station: StationSelection }) {
  const [data, setData] = useState<StationAgeCatchments | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [groupSet, setGroupSet] = useState<'three_stage' | 'planning'>('three_stage');
  useEffect(() => {
    let current = true;
    loadStationAges().then((value) => { if (current) { setData(value); setError(false); } }, () => { if (current) setError(true); });
    return () => { current = false; };
  }, [attempt]);
  const value = data?.records[station.type]?.[station.id]?.[data.radii.indexOf(STATION_RADIUS)];
  const metadata = data?.metadata;
  const groups = metadata?.age_groups.filter((group) => group.set === groupSet) ?? [];
  return <section className="station-age" aria-busy={!data && !error} aria-live="polite">
    <div className="station-section-head"><h3>村里年齡分攤</h3><span className="station-badge">面積估算</span></div>
    {error ? <div className="station-empty"><b>年齡估算載入失敗</b><button className="station-text-button" onClick={() => { setError(false); setAttempt((value) => value + 1); }}>重新載入年齡估算</button></div>
      : !data ? <p className="station-empty" role="status">年齡估算載入中…</p>
        : !value ? <div className="station-empty"><p>尚未提供此站範圍的年齡估算</p><small>缺資料不等於 0 人。</small></div>
          : <>
            <p className="station-meta">{metadata!.population_period} 戶籍人口 · 直線 {STATION_RADIUS} m</p>
            {value.status !== 'complete' && <p className="station-warning">{value.status === 'no_boundary' ? '此站圈沒有可用里界。' : value.status === 'geometry_conflict' ? '里界幾何重疊，衝突部分不分攤。' : '資料僅涵蓋部分站圈。'}以下僅列已知部分，不能視為完整站圈人口或比例。</p>}
            {value.status === 'complete' && value.population_estimate !== null && <p className="station-age-total">全齡約 {estimateText(value.population_estimate)} 人</p>}
            <label className="age-label">年齡分組<select aria-label="站圈年齡分組" value={groupSet} onChange={(event) => setGroupSet(event.target.value as typeof groupSet)}><option value="three_stage">三段年齡</option><option value="planning">細分年齡</option></select></label>
            <StationAgeTable value={value} groups={groups} />
            <p className="station-meta">涵蓋面積占整個 {STATION_RADIUS} m 圓；各組缺值分開扣除。{value.status !== 'complete' && '不以已知部分人口當作完整比例的分母。'}</p>
            <dl className="station-age-coverage"><div><dt>有里界</dt><dd>{percentText(value.boundary_share)}</dd></div><div><dt>已核對村里</dt><dd>{percentText(value.data_share)}</dd></div><div><dt>全齡有值</dt><dd>{percentText(value.population_data_share)}</dd></div></dl>
            {(value.outside_boundary_share > 0 || value.unmatched_village_count > 0 || value.unknown_village_count > 0 || value.geometry_conflict_village_count > 0) && <p className="station-warning">界外／無里界面積 {percentText(value.outside_boundary_share)}；未匹配 {value.unmatched_village_count} 里、人口缺值 {value.missing_population_village_count} 里、資料不完整或未核對 {value.unknown_village_count} 里、幾何衝突 {value.geometry_conflict_village_count} 里。未涵蓋部分人口未知。</p>}
            <details className="station-more"><summary>年齡估算來源與限制</summary><p>人口 {metadata!.population_period}、清冊 {metadata!.roster_period}、里界 {metadata!.boundary_vintage}。{!metadata!.same_month_geometry_verified && '里界與人口月份不同，尚未證明同月幾何一致。'}</p><ul>{metadata!.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul><p>資料來源：內政部戶政司、內政部國土測繪中心。政府資料開放授權條款第 1 版。</p></details>
          </>}
    <p className="station-warning">假設同一村里各年齡人口均勻分布；各站圈重疊，不可加總。這是戶籍人口估算，非搭乘量或實際乘車需求。</p>
  </section>;
}

/** Present the same explicit null/zero and partial-data rules in the station card and tests. */
export function StationAgeTable({ value, groups }: { value: AgeCatchment; groups: AgeGroup[] }) {
  return <table className="station-age-table"><caption className="sr-only">{value.status === 'complete' ? '站圈年齡估算' : '站圈年齡的已知部分估算'}，各組另列涵蓋面積</caption><thead><tr><th scope="col">年齡</th><th scope="col">{value.status === 'complete' ? '估算人數／比例' : '已知部分人數'}</th><th scope="col">涵蓋面積</th></tr></thead><tbody>
              {groups.map((group) => {
                const result = catchmentGroupValue(value, group.key);
                const count = result.count ?? result.known;
                return <tr key={group.key}><th scope="row">{group.label}</th><td>{count === null ? '未知' : `約 ${estimateText(count)} 人`}{result.share !== null && <small>{percentText(result.share)}</small>}{value.status === 'complete' && value.population_estimate === 0 && <small>比例無分母</small>}</td><td>{percentText(result.coverage)}</td></tr>;
              })}
            </tbody></table>;
}
