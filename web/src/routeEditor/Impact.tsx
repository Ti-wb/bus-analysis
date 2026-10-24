import { useState } from 'react';
import { duration } from './engine';
import { ACTION_LABELS, BANDS, type Assessment, type Draft, type ScenarioData } from './types';

export const count = (n: number) => Math.round(n).toLocaleString('zh-TW');
export const signed = (n: number) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${count(Math.abs(n))}`;
export const rate = (n: number | null) => n === null || !Number.isFinite(n) ? '未提供' : Number(n.toFixed(1)).toLocaleString('zh-TW');
const populationRange = (known: number, possible: number) => Math.round(possible) > Math.round(known) ? `${count(known)}–${count(possible)}` : count(known);

export function PopulationSummary({ result, applied, busy }: { result: Assessment | null; applied: boolean; busy: boolean }) {
  return <section className="card route-population" aria-label="評估範圍人口">
    <h3>評估範圍・{applied ? '目前方案' : '現況路網'}</h3>
    <p className="muted">臺北市 · 公車／捷運 350 m 可達人口</p>
    <p className="route-population-value">{result ? populationRange(result.after, result.afterPossible) : '—'} <small>人</small></p>
    <div className="route-population-row"><span className="route-tag senior">65 歲以上</span><b>— 未估算</b></div>
    <div className="route-population-row"><span className="route-tag student">學生</span><b>— 未估算</b></div>
    <p className="route-note">路網人口去重 · 2024-12 戶籍人口。區間上限含缺班表路線的可能覆蓋；高齡與就學身分資料未接入。</p>
    {busy && <p role="status" className="route-note">更新試算中…</p>}
  </section>;
}

export function Duration({ draft, data, speed, dwell }: { draft: Draft; data: ScenarioData; speed: number; dwell: number }) {
  const current = duration(draft.route, data, speed, dwell);
  const original = draft.original && duration(draft.original, data, speed, dwell);
  return <div className="route-duration"><span className="muted">草圖預估單程・含行駛與停站</span>
    <strong>{draft.kind === 'delete' ? '刪除後不適用' : `${original !== null ? `${original} → ` : ''}${current ?? '—'} 分鐘`}</strong>
    <small>{draft.kind === 'delete' ? '停駛不視為節省行程時間。' : `以圖上路徑 ${speed} km/h、每站 ${dwell} 秒假設試算；可在偏好調整。`}</small>
  </div>;
}

export default function Impact({ draft, data, result, busy, error, review, speed, dwell, onBack, onApply, onRetry, onLocate }: {
  draft: Draft; data: ScenarioData; result: Assessment | null; busy: boolean; error: string; review: boolean; speed: number; dwell: number;
  onBack: () => void; onApply: () => void; onRetry: () => void; onLocate: (at: [number, number]) => void;
}) {
  const [all, setAll] = useState(false);
  const lostFacilities = result?.facilities.filter((f) => f.status === 'loss').length ?? 0;
  return <section className="card route-impact" aria-label={review ? '已套用影響回顧' : '影響預覽'} aria-busy={busy}>
    <div className="route-impact-body">
      <p className="route-eyebrow">{review ? '已套用方案・影響回顧' : `${ACTION_LABELS[draft.kind]}・影響預覽`}</p>
      <h2>{draft.route.name}</h2>
      <p className="muted route-note">與原始現況路網比較 · {BANDS.find((b) => b.id === draft.band)!.label}</p>
      {busy && <div className="route-notice" role="status">正在比較人口、設施與班次…<br />完成後才能套用，返回會保留草稿。</div>}
      {error && <div className="route-notice danger" role="alert">{error} <button className="link-btn" onClick={onRetry}>重新試算</button></div>}
      {result && !busy && <>
        <div className="route-impact-pop">
          <p className="muted">公車／捷運 350 m 可達人口・可確認淨變化</p>
          <strong className={result.gained >= result.lost ? 'route-positive' : 'route-negative'}>{signed(result.gained - result.lost)} <small>人</small></strong>
          <p className="muted">{populationRange(result.before, result.beforePossible)} → {populationRange(result.after, result.afterPossible)} 人</p>
        </div>
        <div className="route-demographics">
          <div className="senior"><b>65 歲以上</b><strong>— 未估算</strong><small>缺少對應人口資料</small></div>
          <div className="student"><b>學生（就學身分）</b><strong>— 未估算</strong><small>不以年齡代理學生</small></div>
        </div>
        <div className="route-gain-loss"><b className="route-positive">新增可達 {count(result.gained)} 人</b><b className="route-negative">失去可達 {count(result.lost)} 人</b></div>
        <p className="route-note">仍可達但已知公車班次下降：{count(result.decreased)} 人。</p>
        {(result.uncertain > .5 || result.missingCells > 0) && <p className="route-notice">評估尚不完整：{count(result.uncertain)} 人的變化受缺班表影響；{result.missingCells} 格缺人口。未知項目不計為 0。</p>}
        <div className="route-section-title"><h3>重要設施</h3><small>公車班次限 350 m 內</small></div>
        <div className="route-facilities">
          {result.facilities.slice(0, all ? undefined : 3).map((f) => <button className="route-facility" key={f.id} onClick={() => onLocate(f.at)}>
            <span><b>{f.name}</b><em className={`route-tag ${['loss', 'decrease'].includes(f.status) ? 'loss' : ''}`}>{({ gain: '新增可達', loss: '無近鄰替代', increase: '服務增加', decrease: '班次減少', same: '維持可達', uncertain: '班表待確認', outside: '超過 350 m' })[f.status]}</em></span>
            <small>最近站 {f.before.distance === null ? '—' : count(f.before.distance)} → {f.after.distance === null ? '—' : count(f.after.distance)} m</small>
            <small>{f.before.unknown ? '≥ ' : ''}{rate(f.before.frequency)} → {f.after.unknown ? '≥ ' : ''}{rate(f.after.frequency)} 班/h{f.after.mrt ? ' · 捷運可達（班次未計）' : ''}</small>
          </button>)}
          {!result.facilities.length && <p className="route-note">本次路線周邊 1 km 未找到具名設施候選。</p>}
        </div>
        {result.facilities.length > 3 && <button className="link-btn" onClick={() => setAll((v) => !v)}>{all ? '收合設施' : `查看全部 ${result.facilities.length} 處設施`}</button>}
        <p className={`route-notice${lostFacilities || result.lost > .5 ? ' danger' : ''}`}>{lostFacilities ? `${lostFacilities} 處設施失去 350 m 內替代服務。` : '保留其他公車與捷運的替代服務。'} 缺班表的班次為下限。</p>
        <Duration draft={draft} data={data} speed={speed} dwell={dwell} />
        <p className="route-note">直線可達、人口分攤與班次假設試算；設施為候選位置。YouBike 1 km 另列，不替代步行可達。路徑可行性仍須核實。</p>
      </>}
    </div>
    <footer className="route-impact-footer">
      <div className="route-actions"><button className="btn-outline" onClick={onBack}>{review ? '關閉影響回顧' : draft.kind === 'delete' ? '取消刪除' : '返回編輯'}</button>
        {!review && <button className={`route-primary${draft.kind === 'delete' ? ' danger' : ''}`} disabled={busy || !result || !!error} onClick={onApply}>{({ add: '套用新增路線', edit: '套用路線變更', delete: '確認刪除此路線' })[draft.kind]}</button>}</div>
      <small>{review ? '關閉後保留目前方案；復原請回路線工具。' : '僅套用至此裝置的模擬方案，不變更實際營運路線。'}</small>
    </footer>
  </section>;
}
