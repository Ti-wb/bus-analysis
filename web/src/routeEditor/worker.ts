import { assess } from './engine';
import type { Band, Changes, ScenarioData } from './types';
import type { MapData } from '../types';

let data: ScenarioData;
let map: MapData;
self.onmessage = (event: MessageEvent<{ id: number; data?: ScenarioData; map?: MapData; changes: Changes; band: Band }>) => {
  const request = event.data;
  if (request.data) data = request.data;
  if (request.map) map = request.map;
  try {
    self.postMessage({ id: request.id, result: assess(data, map, request.changes, request.band) });
  } catch {
    self.postMessage({ id: request.id, error: '無法完成影響試算，請重新試算。草稿仍保留。' });
  }
};
