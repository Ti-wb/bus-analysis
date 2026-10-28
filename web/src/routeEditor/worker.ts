import { assess } from './engine';
import type { AssessmentMap, Band, Changes, ScenarioData } from './types';

export type AssessmentRequest = { type: 'init'; data: ScenarioData; map: AssessmentMap }
  | { type: 'assess'; id: number; changes: Changes; band: Band };

let data: ScenarioData;
let map: AssessmentMap;
self.onmessage = (event: MessageEvent<AssessmentRequest>) => {
  const request = event.data;
  if (request.type === 'init') {
    data = request.data;
    map = request.map;
    return;
  }
  try {
    self.postMessage({ id: request.id, result: assess(data, map, request.changes, request.band) });
  } catch {
    self.postMessage({ id: request.id, error: '無法完成影響試算，請重新試算。草稿仍保留。' });
  }
};
