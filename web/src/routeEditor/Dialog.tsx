import { useId, type ReactNode } from 'react';
import Modal, { type DismissModal } from '../components/Modal';

export default function Dialog({ title, children, onClose }: { title: string; children: ReactNode | ((dismiss: DismissModal) => ReactNode); onClose: () => void }) {
  const id = useId();
  return <Modal className="route-dialog" labelledBy={id} onClose={onClose}>{(dismiss) => <>
    <header><h2 id={id}>{title}</h2><button className="icon-btn" aria-label="關閉對話框" onClick={() => dismiss()}>✕</button></header>
    {typeof children === 'function' ? children(dismiss) : children}
  </>}</Modal>;
}
