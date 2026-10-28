import { useEffect, useMemo, useState } from 'react';

/** Keep results/errors tied to their request, including during the render before effects run. */
export function useData<T>(load: () => Promise<T>, enabled = true) {
  const [attempt, setAttempt] = useState(0);
  const request = useMemo(() => ({ load, attempt }), [load, attempt]);
  const [result, setResult] = useState<{ request: typeof request; data?: T; error?: Error } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    load().then(
      (data) => { if (current) setResult({ request, data }); },
      (reason: unknown) => { if (current) setResult({ request, error: reason instanceof Error ? reason : new Error(String(reason)) }); },
    );
    return () => { current = false; };
  }, [request, enabled, load]);
  const data = result?.request === request ? result.data ?? null : null;
  const error = enabled && result?.request === request ? result.error ?? null : null;
  return { data, error, loading: enabled && data === null && !error, retry: () => setAttempt((value) => value + 1) };
}
