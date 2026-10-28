import { useEffect, useMemo, useRef, useState } from 'react';
import type { Assessment, AssessmentMap, Band, Changes, ScenarioData } from './types';
import type { AssessmentRequest } from './worker';

const FAILURE = '試算暫時無法執行，請重試。草稿仍保留。';

export function useAssessment(data: ScenarioData, map: AssessmentMap, changes: Changes, band: Band, retry: number) {
  const request = useMemo(() => ({ data, map, changes, band, retry }), [data, map, changes, band, retry]);
  const [response, setResponse] = useState<{ request: typeof request; result?: Assessment; error?: string } | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const sequence = useRef(0);

  // Transfer only the calculation inputs, once per workspace (or explicit retry).
  useEffect(() => {
    let worker: Worker | null = null;
    try {
      worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
      worker.postMessage({ type: 'init', data, map } satisfies AssessmentRequest);
      workerRef.current = worker;
    } catch {
      worker?.terminate();
      workerRef.current = null;
    }
    return () => { worker?.terminate(); workerRef.current = null; };
  }, [data, map, retry]);

  useEffect(() => {
    const worker = workerRef.current;
    if (!worker) { setResponse({ request, error: FAILURE }); return; }
    const id = ++sequence.current;
    const fail = (event?: Event) => {
      event?.preventDefault();
      worker.terminate();
      workerRef.current = null;
      setResponse({ request, error: FAILURE });
    };
    worker.onmessage = (event: MessageEvent<{ id: number; result?: Assessment; error?: string }>) => {
      if (event.data.id === id) setResponse({ request, result: event.data.result, error: event.data.error });
    };
    worker.onerror = fail;
    worker.onmessageerror = fail;
    const timer = setTimeout(() => {
      try { worker.postMessage({ type: 'assess', id, changes, band } satisfies AssessmentRequest); }
      catch { fail(); }
    }, 100);
    return () => {
      clearTimeout(timer);
      worker.onmessage = worker.onerror = worker.onmessageerror = null;
    };
  }, [data, map, request, changes, band]);

  const busy = response?.request !== request;
  return { busy, result: !busy ? response?.result ?? null : null, error: !busy ? response?.error ?? '' : '' };
}
