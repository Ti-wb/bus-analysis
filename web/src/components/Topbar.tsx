import { useState } from 'react';
import type { AnalysisMode } from '../types';

interface Props {
  mode: AnalysisMode;
  aboutActive: boolean;
  onAbout: () => void;
  onShare: () => Promise<boolean>;
}

export default function Topbar({ mode, aboutActive, onAbout, onShare }: Props) {
  const [shareText, setShareText] = useState('分享視圖');
  const share = async () => {
    setShareText((await onShare()) ? '已複製連結' : '請複製網址列');
    setTimeout(() => setShareText('分享視圖'), 1600);
  };

  return (
    <header className="topbar">
      <a className="brand" href="./">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" d="M12 3 3 8l9 5 9-5-9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5" />
          </svg>
        </span>
        <span>覆蓋臺北</span>
      </a>
      <nav className="tabs">
        <button className={`tab${aboutActive ? '' : ' is-active'}`}>探索地圖</button>
        <button className={`tab${aboutActive ? ' is-active' : ''}`} onClick={onAbout}>資料與方法</button>
      </nav>
      <div className="topbar-right">
        <span className="chip chip-teal">{mode === 'frequency' ? '公車班次網格' : '雙層站點覆蓋'}</span>
        <span className="chip chip-amber">{mode === 'frequency' ? '計畫班表，非實際運量' : '直線半徑，非實際路網'}</span>
        <button className="btn-outline" onClick={share}>{shareText}</button>
      </div>
    </header>
  );
}
