import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import logo from '../assets/logo-horizontal.svg';
import { TOUR_STEPS } from './content';
import Illustration from './Illustration';
import './onboarding.css';

export default function Onboarding({ onFinish }: { onFinish: () => void }) {
  const [step, setStep] = useState(0);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const content = TOUR_STEPS[step];

  useEffect(() => {
    const dialog = dialogRef.current!;
    const origin = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => {
      dialog.close();
      // A replay sits above Data & methods, leaving its entry and the workspace intact.
      if (origin?.isConnected) origin.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    dialogRef.current?.scrollTo(0, 0);
    titleRef.current?.focus({ preventScroll: true });
  }, [step]);

  const handleKey = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onFinish();
    }
    if (event.key !== 'Tab') return;
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
    const first = buttons[0];
    const last = buttons.at(-1)!;
    const active = document.activeElement;
    if (!buttons.includes(active as HTMLButtonElement) || active === (event.shiftKey ? first : last)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  };

  return <dialog ref={dialogRef} className="onboarding" aria-labelledby="tour-title" onKeyDown={handleKey}
    onCancel={(event) => { event.preventDefault(); onFinish(); }}>
    <div className="tour-shell">
      <header className="tour-header">
        <img src={logo} alt="Bus coming soon" width="174" height="40" />
        <p>快速導覽 / 從看見問題，到一起改善</p>
        <button className="tour-button tour-skip" onClick={onFinish}>略過導覽</button>
      </header>
      <section className="tour-card" aria-label="快速導覽">
        <ol className="tour-progress" aria-label="導覽步驟">
          {TOUR_STEPS.map((item, index) => <li key={item.label} aria-current={index === step ? 'step' : undefined}>
            <span>{String(index + 1).padStart(2, '0')}</span>{item.label}
          </li>)}
        </ol>
        <div className="tour-content">
          <Illustration step={step} />
          <div className="tour-copy">
            <span className="tour-tag">{content.label}</span>
            <h1 id="tour-title" ref={titleRef} tabIndex={-1}><span className="tour-sr-only">快速導覽，第 {step + 1} 步，共 4 步。{content.label}。</span>{content.title}</h1>
            <p className="tour-introduction">{content.introduction}</p>
            <ol className="tour-points">{content.points.map(([title, description], index) => <li key={title}>
              <span aria-hidden="true">{index + 1}</span><div><h2>{title}</h2><p>{description}</p></div>
            </li>)}</ol>
          </div>
        </div>
        <p className="tour-limitation">{content.limitation}</p>
        <footer className="tour-footer">
          <span aria-hidden="true">{String(step + 1).padStart(2, '0')} / 04</span>
          {step > 0 && <button className="tour-button tour-back" onClick={() => setStep((value) => value - 1)}>上一步</button>}
          <button className="tour-button tour-next" onClick={() => step === TOUR_STEPS.length - 1 ? onFinish() : setStep((value) => value + 1)}>{content.next}</button>
        </footer>
      </section>
      <p className="tour-replay-note">隨時從「資料與方法 → 快速導覽」重新開啟。</p>
    </div>
  </dialog>;
}
