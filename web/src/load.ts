/** Share pending/successful requests. A rejection must allow the next call to retry. */
export function cachedLoader<K, T>(load: (key: K) => Promise<T>): (key: K) => Promise<T> {
  const cache = new Map<K, Promise<T>>();
  return (key) => {
    let pending = cache.get(key);
    if (!pending) {
      pending = Promise.resolve().then(() => load(key)).catch((error: unknown) => {
        cache.delete(key);
        throw error;
      });
      cache.set(key, pending);
    }
    return pending;
  };
}

export function retryableLoader<T>(load: () => Promise<T>): () => Promise<T> {
  const read = cachedLoader((_key: undefined) => load());
  return () => read(undefined);
}
