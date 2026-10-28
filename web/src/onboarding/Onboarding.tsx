import { useEffect, useRef, useState } from 'react';
import Modal from '../components/Modal';
import logo from '../assets/logo-horizontal.svg';
import { TOUR_STEPS } from './content';
import Illustration from './Illustration';
import './onboarding.css';

export default function Onboarding({ onFinish }: { onFinish: () => void }) {
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState(1);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const content = TOUR_STEPS[step];

  useEffect(() => {
    dialogRef.current?.scrollTo(0, 0);
    titleRef.current?.focus({ preventScroll: true });
  }, [step]);

  return <Modal dialogRef={dialogRef} className="onboarding" labelledBy="tour-title" onClose={onFinish}>{(dismiss) => <>
    <div className="tour-shell">
      <header className="tour-header">
        <img src={logo} alt="Bus coming soon" width="174" height="40" />
        <p>快速導覽 / 從看見問題，到一起改善</p>
        <button className="tour-button tour-skip" onClick={() => dismiss()}>略過導覽</button>
      </header>
      <section className="tour-card" aria-label="快速導覽">
        <ol className="tour-progress" aria-label="導覽步驟">
          {TOUR_STEPS.map((item, index) => <li key={item.label} aria-current={index === step ? 'step' : undefined}>
            <span>{String(index + 1).padStart(2, '0')}</span>{item.label}
          </li>)}
        </ol>
        <div className="tour-content" key={step} data-direction={direction}>
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
          {step > 0 && <button className="tour-button tour-back" onClick={() => { setDirection(-1); setStep((value) => value - 1); }}>上一步</button>}
          <button className="tour-button tour-next" onClick={() => { if (step === TOUR_STEPS.length - 1) dismiss(); else { setDirection(1); setStep((value) => value + 1); } }}>{content.next}</button>
        </footer>
      </section>
      <p className="tour-replay-note">隨時從「資料與方法 → 快速導覽」重新開啟。</p>
    </div>
  </>}</Modal>;
}
