import assert from 'node:assert/strict';
import test from 'node:test';
import { INITIAL_MAP_LOAD, MAP_LOAD_TIMEOUT, mapLoadReducer, type MapLoadEvent, type MapLoadState } from '../src/map/loadState';

type AttemptEvent = Exclude<MapLoadEvent, { type: 'retry' }>['type'];
const send = (state: MapLoadState, type: AttemptEvent) => mapLoadReducer(state, { type, attempt: state.attempt });

test('normal map load becomes ready and an expired load timer cannot replace the working map', () => {
  assert.equal(MAP_LOAD_TIMEOUT, 12_000);
  assert.deepEqual(INITIAL_MAP_LOAD, { base: 'online', status: 'loading', attempt: 0, issue: null });
  const ready = send(INITIAL_MAP_LOAD, 'loaded');
  assert.deepEqual(ready, { base: 'online', status: 'ready', attempt: 0, issue: null });
  assert.equal(send(ready, 'timeout'), ready);
  assert.equal(INITIAL_MAP_LOAD.status, 'loading');
});

test('a source error during initial online loading warns without pretending that the map is ready', () => {
  const warning = send(INITIAL_MAP_LOAD, 'resource-error');
  assert.equal(warning.status, 'loading');
  assert.equal(warning.issue, 'resource');
  const ready = send(warning, 'loaded');
  assert.equal(ready.status, 'ready');
  assert.equal(ready.issue, 'resource');
  const dismissed = send(ready, 'dismiss');
  assert.equal(dismissed.status, 'ready');
  assert.equal(dismissed.issue, null);
});

test('a layer resource error after load keeps a usable map ready and does not trigger timeout fallback', () => {
  const ready = send(INITIAL_MAP_LOAD, 'loaded');
  const warning = send(ready, 'resource-error');
  assert.deepEqual(warning, { ...ready, issue: 'resource' });
  assert.equal(send(warning, 'timeout'), warning);
  assert.equal(send(warning, 'dismiss').status, 'ready');
});

test('online timeout tries local once, then local timeout terminates loading as a failure', () => {
  const local = send(send(INITIAL_MAP_LOAD, 'resource-error'), 'timeout');
  assert.deepEqual(local, { base: 'local', status: 'loading', attempt: 1, issue: null });
  // A success from the replaced online instance cannot complete the local attempt.
  assert.equal(mapLoadReducer(local, { type: 'loaded', attempt: 0 }), local);
  const failed = send(local, 'timeout');
  assert.deepEqual(failed, { base: 'local', status: 'failed', attempt: 1, issue: 'timeout' });
  assert.equal(send(failed, 'timeout'), failed);
  assert.equal(send(failed, 'loaded'), failed);
});

test('successful local fallback completes only its own attempt and remains local after timeout', () => {
  const local = send(INITIAL_MAP_LOAD, 'timeout');
  const ready = send(local, 'loaded');
  assert.deepEqual(ready, { base: 'local', status: 'ready', attempt: 1, issue: null });
  assert.equal(send(ready, 'timeout'), ready);
});

test('initialization failure is terminal until retry and never reports ready on a late load', () => {
  const failed = send(INITIAL_MAP_LOAD, 'initialization-error');
  assert.deepEqual(failed, { base: 'online', status: 'failed', attempt: 0, issue: 'initialization' });
  for (const type of ['loaded', 'timeout', 'resource-error', 'initialization-error', 'context-lost', 'dismiss'] as const) {
    assert.equal(send(failed, type), failed, `failed attempt must ignore ${type}`);
  }
});

test('retry selects its requested basemap, increments the attempt and rejects every stale event', () => {
  const failed = send(INITIAL_MAP_LOAD, 'initialization-error');
  const local = mapLoadReducer(failed, { type: 'retry', base: 'local' });
  assert.deepEqual(local, { base: 'local', status: 'loading', attempt: 1, issue: null });
  for (const type of ['loaded', 'timeout', 'resource-error', 'initialization-error', 'context-lost', 'dismiss'] as const) {
    assert.equal(mapLoadReducer(local, { type, attempt: 0 }), local, `retry must ignore stale ${type}`);
  }
  const online = mapLoadReducer(send(local, 'loaded'), { type: 'retry', base: 'online' });
  assert.deepEqual(online, { base: 'online', status: 'loading', attempt: 2, issue: null });
  assert.equal(mapLoadReducer(online, { type: 'loaded', attempt: 1 }), online);
  assert.equal(send(online, 'loaded').status, 'ready');
});

test('local source errors terminate loading, while errors after local readiness remain warnings', () => {
  const local = mapLoadReducer(INITIAL_MAP_LOAD, { type: 'retry', base: 'local' });
  const failed = send(local, 'resource-error');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.issue, 'initialization');
  assert.equal(send(failed, 'loaded'), failed);
  const ready = send(local, 'loaded');
  const warning = send(ready, 'resource-error');
  assert.equal(warning.status, 'ready');
  assert.equal(warning.issue, 'resource');
});

test('WebGL context loss fails a previously ready map and a new attempt can recover', () => {
  const ready = send(INITIAL_MAP_LOAD, 'loaded');
  const failed = send(ready, 'context-lost');
  assert.deepEqual(failed, { ...ready, status: 'failed', issue: 'context' });
  assert.equal(send(failed, 'loaded'), failed);
  const retry = mapLoadReducer(failed, { type: 'retry', base: 'online' });
  assert.equal(retry.status, 'loading');
  assert.equal(retry.issue, null);
  assert.equal(send(retry, 'loaded').status, 'ready');
});
