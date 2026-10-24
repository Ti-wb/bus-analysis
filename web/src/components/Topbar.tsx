import { useState } from 'react';
import logoHorizontal from '../assets/logo-horizontal.svg';
import logoSymbol from '../assets/logo-symbol.svg';
import type { AnalysisMode } from '../types';

const CHIPS: Record<AnalysisMode, [string, string]> = {
  frequency: ['公車班次網格', '計畫班表，非實際運量'],
  coverage: ['雙層站點覆蓋', '直線半徑，非實際路網'],
  need: ['需求落差', '研究草案，權重為假設'],
  village_age: ['村里年齡', '戶籍背景，非乘車需求'],
};

interface Props {
  mode: AnalysisMode;
  aboutActive: boolean;
  onAbout: () => void;
  onPreferences: () => void;
  onShare: () => Promise<boolean>;
  routeTool?: boolean;
  stationFocus?: boolean;
  improvements?: boolean;
  onImprovements: () => void;
  onExport: () => void;
  onRoutes: () => void;
  onExplore: () => void;
  onIndicators: () => void;
}

export default function Topbar({ mode, aboutActive, onAbout, onPreferences, onShare, routeTool, stationFocus, improvements, onImprovements, onExport, onRoutes, onExplore, onIndicators }: Props) {
  const [shareText, setShareText] = useState('分享視圖');
  const share = async () => {
    setShareText((await onShare()) ? '已複製連結' : '請複製網址列');
    setTimeout(() => setShareText('分享視圖'), 1600);
  };

  return (
    <header className="topbar">
      {/* Brand guidelines: horizontal lockup in the header, symbol only where the bar is narrow. */}
      <a className="brand" href="./" aria-label="Bus coming soon 首頁" onClick={(e) => { if (routeTool || improvements) { e.preventDefault(); onExplore(); } }}>
        <img className="brand-logo" src={logoHorizontal} alt="" width="174" height="40" />
        <img className="brand-symbol" src={logoSymbol} alt="" width="42" height="32" />
      </a>
      <nav className="tabs">
        <button className={`tab${!aboutActive && !routeTool && !improvements ? ' is-active' : ''}`} onClick={onExplore}>探索地圖</button>
        <button className={`tab${!aboutActive && improvements ? ' is-active' : ''}`} onClick={onImprovements}>改善清單</button>
        <button className={`tab${!aboutActive && routeTool ? ' is-active' : ''}`} onClick={onRoutes}>路線工具</button>
        <button className={`tab${aboutActive ? ' is-active' : ''}`} onClick={onAbout}>資料與方法</button>
      </nav>
      <div className="topbar-right">
        {!routeTool && !improvements && <span className="chip chip-accent">{stationFocus ? '站點 Focus' : CHIPS[mode][0]}</span>}
        <span className="chip chip-amber">{improvements ? '⚠ 部分指標為 Mock Data' : routeTool ? '模擬方案・本機保存' : stationFocus ? '周邊 350 m・本站服務路線' : CHIPS[mode][1]}</span>
        {improvements && <button className="btn-outline" onClick={onExport}>↓ 匯出 CSV</button>}
        <button className="btn-outline btn-prefs" onClick={onPreferences}>
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <path fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" d="M3 2v12M8 2v12M13 2v12M1.5 10h3M6.5 5h3M11.5 9h3" />
          </svg>
          偏好
        </button>
        <button className="btn-outline" onClick={routeTool ? onIndicators : share}>{routeTool ? '指標說明' : shareText}</button>
      </div>
    </header>
  );
}
