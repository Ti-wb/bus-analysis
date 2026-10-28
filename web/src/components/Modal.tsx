import { useLayoutEffect, useRef, type CSSProperties, type ReactNode, type RefObject } from 'react';
import { motionDuration } from '../motion';

export type DismissModal = (afterClose?: () => void) => void;

interface Props {
  className: string;
  labelledBy: string;
  onClose: () => void;
  dismissOnBackdrop?: boolean;
  dialogRef?: RefObject<HTMLDialogElement | null>;
  children: ReactNode | ((dismiss: DismissModal) => ReactNode);
}

/** Native modality owns focus/inertness; the short exit finishes before restoring the opener. */
export default function Modal({ className, labelledBy, onClose, dismissOnBackdrop = false, dialogRef, children }: Props) {
  const ownRef = useRef<HTMLDialogElement>(null);
  const ref = dialogRef ?? ownRef;
  const closing = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const backdropDown = useRef(false);

  useLayoutEffect(() => {
    const dialog = ref.current!;
    const origin = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closing.current = false;
    delete dialog.dataset.closing;
    dialog.showModal();
    return () => {
      clearTimeout(timer.current);
      dialog.close();
      if (origin?.isConnected) origin.focus({ preventScroll: true });
    };
  }, [ref]);

  const dismiss: DismissModal = (afterClose = onClose) => {
    if (closing.current) return;
    closing.current = true;
    const dialog = ref.current!;
    const current = getComputedStyle(dialog);
    dialog.style.setProperty('--exit-opacity', current.opacity);
    dialog.style.setProperty('--exit-transform', current.transform);
    dialog.style.setProperty('--backdrop-exit-opacity', getComputedStyle(dialog, '::backdrop').opacity);
    dialog.dataset.closing = 'true';
    const finish = () => {
      ref.current?.close();
      afterClose();
    };
    const duration = motionDuration(140);
    if (duration) timer.current = setTimeout(finish, duration);
    else finish();
  };
  const outside = (event: { target: EventTarget; clientX: number; clientY: number }) => {
    const dialog = ref.current!;
    const box = dialog.getBoundingClientRect();
    return event.target === dialog && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom);
  };

  return <dialog ref={ref} className={`modal ${className}`} aria-labelledby={labelledBy}
    style={{ '--modal-exit': '140ms' } as CSSProperties}
    onKeyDown={(event) => { if (event.key === 'Escape') event.stopPropagation(); }}
    onCancel={(event) => { event.preventDefault(); event.stopPropagation(); dismiss(); }}
    onPointerDown={(event) => { backdropDown.current = outside(event); }}
    onClick={(event) => { if (dismissOnBackdrop && backdropDown.current && outside(event)) dismiss(); }}>
    {typeof children === 'function' ? children(dismiss) : children}
  </dialog>;
}
