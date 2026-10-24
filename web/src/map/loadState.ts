export type MapStatus = 'loading' | 'ready' | 'failed';
export type Basemap = 'online' | 'local';
export interface MapLoadState {
  base: Basemap;
  status: MapStatus;
  attempt: number;
  issue: 'resource' | 'timeout' | 'initialization' | 'context' | null;
}
export const INITIAL_MAP_LOAD: MapLoadState = { base: 'online', status: 'loading', attempt: 0, issue: null };
export const MAP_LOAD_TIMEOUT = 12_000;
export type MapLoadEvent =
  | { type: 'retry'; base: Basemap }
  | { type: 'loaded' | 'timeout' | 'resource-error' | 'initialization-error' | 'context-lost' | 'dismiss'; attempt: number };

/** Errors after load can affect one source only. They must not restart loading or discard the map. */
export function mapLoadReducer(state: MapLoadState, event: MapLoadEvent): MapLoadState {
  if (event.type === 'retry') return { base: event.base, status: 'loading', attempt: state.attempt + 1, issue: null };
  if (event.attempt !== state.attempt || state.status === 'failed') return state;
  switch (event.type) {
    case 'loaded': return { ...state, status: 'ready' };
    case 'timeout':
      if (state.status !== 'loading') return state;
      return state.base === 'online'
        ? { base: 'local', status: 'loading', attempt: state.attempt + 1, issue: null }
        : { ...state, status: 'failed', issue: 'timeout' };
    case 'initialization-error': return { ...state, status: 'failed', issue: 'initialization' };
    case 'context-lost': return { ...state, status: 'failed', issue: 'context' };
    case 'resource-error': return state.base === 'local' && state.status === 'loading'
      ? { ...state, status: 'failed', issue: 'initialization' }
      : { ...state, issue: 'resource' };
    case 'dismiss': return { ...state, issue: null };
  }
}
