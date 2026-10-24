import { useEffect, useRef, type ReactNode } from 'react';

export default function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current!; d.showModal(); return () => d.close(); }, []);
  return <dialog ref={ref} className="route-dialog" aria-labelledby="route-dialog-title" onCancel={(e) => { e.preventDefault(); onClose(); }}>
    <header><h2 id="route-dialog-title">{title}</h2><button className="icon-btn" aria-label="關閉對話框" onClick={onClose}>✕</button></header>
    {children}
  </dialog>;
}
