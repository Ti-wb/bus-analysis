import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';

const DATA = `${import.meta.env.BASE_URL}data/`;
const ALL_DAY = 24;
const DAY_LABELS = { weekday: '平日', saturday: '週六', sunday: '週日' };
const COLORS = ['#f1d6cf', '#ecdcb8', '#cfe3d6', '#a6d2c9', '#6fb8ad', '#2f8f86'];
// Breaks follow the value quantiles of each cell size, so larger cells get a wider scale.
const SCALES = {
  250: {
    hourly: { breaks: [0, 1, 6, 16, 41, 81], labels: ['0', '1–5', '6–15', '16–40', '41–80', '81+'] },
    daily: { breaks: [0, 1, 51, 201, 501, 1001], labels: ['0', '1–50', '51–200', '201–500', '501–1k', '1k+'] },
  },
  500: {
    hourly: { breaks: [0, 1, 11, 31, 61, 101], labels: ['0', '1–10', '11–30', '31–60', '61–100', '101+'] },
    daily: { breaks: [0, 1, 101, 301, 701, 1201], labels: ['0', '1–100', '101–300', '301–700', '701–1.2k', '1.2k+'] },
  },
};
const CELL_SIZES = Object.keys(SCALES).map(Number);
// Cell classes beyond the colour scale.
const NO_STOP = -1;
const NO_SCHEDULE = -2;
const LAYER_GROUPS = {
  grid: ['grid-fill', 'grid-hatch'],
  routes: ['bus-routes'],
  stops: ['bus-stops'],
  mrt: ['mrt-lines-casing', 'mrt-lines', 'mrt-stations'],
};
const HOVER_LAYERS = ['bus-stops', 'mrt-stations', 'bus-routes', 'mrt-lines', 'grid-fill', 'grid-hatch'];

const state = readHash();
const grids = {};
let grid;
let gridFeatures = [];
let playTimer;
const $ = (selector) => document.querySelector(selector);

const map = new maplibregl.Map({
  container: 'map',
  style: { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#f4f3ef' } }] },
  center: state.center,
  zoom: state.zoom,
  minZoom: 9.5,
  maxZoom: 17,
  maxBounds: [[121.2, 24.8], [121.9, 25.4]],
  attributionControl: false,
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
map.addControl(new maplibregl.AttributionControl({
  compact: true,
  customAttribution: '公車：臺北市公共運輸處｜行政界：內政部國土測繪中心｜© OpenStreetMap contributors',
}), 'top-right');

const loadJSON = (name) => fetch(DATA + name).then((r) => {
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
});

map.on('load', async () => {
  // The container can be measured before the stylesheet applies; re-measure once ready.
  map.resize();
  let data;
  try {
    const names = ['green', 'water', 'roads', 'districts', 'taipei_boundary', 'bus_routes', 'bus_stops', 'mrt_lines', 'mrt_stations', 'district_labels'];
    const [docs] = await Promise.all([Promise.all(names.map((n) => loadJSON(`${n}.geojson`))), loadGrid(state.cell)]);
    data = Object.fromEntries(names.map((n, i) => [n, docs[i]]));
  } catch (error) {
    showDataError(error);
    return;
  }
  addHatchPattern();
  addBaseLayers(data);
  addGridLayers();
  addNetworkLayers(data);
  addDistrictLabels(data.district_labels);
  buildSearchIndex(data);
  bindControls();
  await useGrid(state.cell);
  $('#loading').hidden = true;
});

async function loadGrid(cell) {
  grids[cell] ??= await loadJSON(`bus_grid_${cell}.json`);
  return grids[cell];
}

async function useGrid(cell) {
  grid = await loadGrid(cell);
  const [ox, oy] = grid.origin;
  const [sx, sy] = grid.step;
  gridFeatures = grid.cells.map((c, id) => {
    const x0 = ox + c[0] * sx;
    const y0 = oy + c[1] * sy;
    return {
      type: 'Feature',
      id,
      properties: {},
      geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + sx, y0], [x0 + sx, y0 + sy], [x0, y0 + sy], [x0, y0]]] },
    };
  });
  fillAbout();
  render();
}

function addBaseLayers(data) {
  map.addSource('green', { type: 'geojson', data: data.green });
  map.addSource('water', { type: 'geojson', data: data.water });
  map.addSource('roads', { type: 'geojson', data: data.roads });
  map.addSource('districts', { type: 'geojson', data: data.districts });
  map.addSource('taipei', { type: 'geojson', data: data.taipei_boundary });
  map.addLayer({ id: 'green', type: 'fill', source: 'green', paint: { 'fill-color': '#e2eadc', 'fill-opacity': 0.8 } });
  map.addLayer({ id: 'water', type: 'fill', source: 'water', paint: { 'fill-color': '#cfe2e6' } });
}

function addGridLayers() {
  map.addSource('grid', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'grid-fill',
    type: 'fill',
    source: 'grid',
    filter: ['!=', ['get', 'k'], NO_SCHEDULE],
    paint: {
      'fill-color': ['match', ['get', 'k'], NO_STOP, COLORS[0], 0, COLORS[0], 1, COLORS[1], 2, COLORS[2], 3, COLORS[3], 4, COLORS[4], COLORS[5]],
      'fill-opacity': ['case', ['==', ['get', 'k'], NO_STOP], 0.28, 0.82],
      'fill-outline-color': 'rgba(255,255,255,0.7)',
    },
  });
  map.addLayer({ id: 'grid-hatch', type: 'fill', source: 'grid', filter: ['==', ['get', 'k'], NO_SCHEDULE], paint: { 'fill-pattern': 'hatch' } });
}

function addNetworkLayers(data) {
  // Roads sit above the grid so cells read as city blocks, as in the design.
  map.addLayer({
    id: 'roads',
    type: 'line',
    source: 'roads',
    paint: {
      'line-color': '#ffffff',
      'line-opacity': 0.85,
      'line-width': ['interpolate', ['linear'], ['zoom'], 10, ['match', ['get', 'class'], ['motorway', 'trunk'], 1.4, 0.6], 15, ['match', ['get', 'class'], ['motorway', 'trunk'], 6, 3.5]],
    },
  });
  map.addLayer({ id: 'districts', type: 'line', source: 'districts', paint: { 'line-color': '#9aa3a5', 'line-width': 0.8, 'line-dasharray': [3, 2], 'line-opacity': 0.7 } });
  map.addLayer({ id: 'taipei-boundary', type: 'line', source: 'taipei', paint: { 'line-color': '#5d6b6e', 'line-width': 1.6, 'line-opacity': 0.7 } });

  map.addSource('bus-routes', { type: 'geojson', data: data.bus_routes, promoteId: 'route_id' });
  map.addLayer({
    id: 'bus-routes',
    type: 'line',
    source: 'bus-routes',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: {
      'line-color': '#4f6f8f',
      // Thin, translucent lines: stacked routes darken on their own and show where the network piles up.
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.08, 13, 0.16, 16, 0.3],
      'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 15, ['interpolate', ['linear'], ['coalesce', ['get', 'weekday_departures'], 0], 0, 1, 300, 3]],
    },
  });
  map.addLayer({
    id: 'bus-route-highlight',
    type: 'line',
    source: 'bus-routes',
    filter: ['==', ['get', 'route_id'], ''],
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': '#1f8a80', 'line-width': 4 },
  });

  map.addSource('mrt-lines', { type: 'geojson', data: data.mrt_lines });
  map.addLayer({ id: 'mrt-lines-casing', type: 'line', source: 'mrt-lines', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 3, 15, 8] } });
  map.addLayer({ id: 'mrt-lines', type: 'line', source: 'mrt-lines', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 15, 5] } });
  map.addSource('mrt-stations', { type: 'geojson', data: data.mrt_stations });
  map.addLayer({
    id: 'mrt-stations',
    type: 'circle',
    source: 'mrt-stations',
    minzoom: 11,
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2.5, 15, 6], 'circle-color': '#ffffff', 'circle-stroke-color': '#3d3f4a', 'circle-stroke-width': 1.5 },
  });

  map.addSource('bus-stops', { type: 'geojson', data: data.bus_stops });
  map.addLayer({
    id: 'bus-stops',
    type: 'circle',
    source: 'bus-stops',
    minzoom: 13.5,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13.5, 2, 17, ['interpolate', ['linear'], ['get', 'route_count'], 1, 4, 30, 9]],
      'circle-color': '#ffffff',
      'circle-stroke-color': '#4f6f8f',
      'circle-stroke-width': 1.4,
    },
  });
}

function addHatchPattern() {
  const size = 8;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = 'rgba(245,245,245,0.9)';
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = '#c8c8c8';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, size);
  ctx.lineTo(size, 0);
  ctx.stroke();
  map.addImage('hatch', ctx.getImageData(0, 0, size, size));
}

function addDistrictLabels(labels) {
  const markers = labels.features.map((f) => {
    const el = document.createElement('div');
    el.className = `district-label${f.properties.city_code === '63000' ? '' : ' is-ntpc'}`;
    el.textContent = f.properties.name;
    return new maplibregl.Marker({ element: el }).setLngLat(f.geometry.coordinates).addTo(map);
  });
  const toggle = () => document.body.classList.toggle('map-hide-labels', map.getZoom() < 10.5 || map.getZoom() > 14.5);
  map.on('zoom', toggle);
  toggle();
  return markers;
}

function scale() {
  return SCALES[state.cell][state.hour === ALL_DAY ? 'daily' : 'hourly'];
}

function classify(value, breaks) {
  let k = 0;
  for (let i = 0; i < breaks.length; i += 1) if (value >= breaks[i]) k = i;
  return k;
}

function cellValue(cell) {
  const series = cell[grid.fields.indexOf(state.day)];
  if (!series) return null;
  return state.hour === ALL_DAY ? series.reduce((a, b) => a + b, 0) : series[state.hour];
}

function render() {
  const { breaks } = scale();
  grid.cells.forEach((cell, i) => {
    const subroutes = cell[3];
    const value = cellValue(cell);
    let k;
    if (!subroutes) k = NO_STOP;
    else if (value === null) k = NO_SCHEDULE;
    else k = classify(value, breaks);
    gridFeatures[i].properties = { k };
  });
  map.getSource('grid').setData({ type: 'FeatureCollection', features: gridFeatures });

  for (const [group, layers] of Object.entries(LAYER_GROUPS)) {
    for (const id of layers) map.setLayoutProperty(id, 'visibility', state.layers.has(group) ? 'visible' : 'none');
  }
  document.querySelectorAll('[data-layer]').forEach((el) => { el.checked = state.layers.has(el.dataset.layer); });
  document.querySelectorAll('[data-day]').forEach((el) => el.classList.toggle('is-active', el.dataset.day === state.day));
  document.querySelectorAll('[data-cell]').forEach((el) => el.classList.toggle('is-active', Number(el.dataset.cell) === state.cell));
  $('#cell-size').textContent = `${state.cell} m`;
  $('#hour').value = state.hour;
  $('#hour-text').textContent = hourText(state.hour);
  $('#status-chip').textContent = `${DAY_LABELS[state.day]} · ${hourText(state.hour)}`;
  renderLegend();
  writeHash();
}

function renderLegend() {
  const { labels } = scale();
  $('#legend-title').textContent = `${state.hour === ALL_DAY ? '全日' : '每小時'}計畫發車數（每 ${state.cell} m 格）`;
  $('#legend-scale').innerHTML = labels.map((label, i) => `<div><span style="background:${COLORS[i]}"></span>${label}</div>`).join('');
}

function hourText(hour) {
  if (hour === ALL_DAY) return '全日';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(hour)}:00–${pad(hour + 1)}:00`;
}

function bindControls() {
  document.querySelectorAll('[data-layer]').forEach((el) => el.addEventListener('change', () => {
    if (el.checked) state.layers.add(el.dataset.layer);
    else state.layers.delete(el.dataset.layer);
    render();
  }));
  document.querySelectorAll('[data-day]').forEach((el) => el.addEventListener('click', () => {
    state.day = el.dataset.day;
    render();
  }));
  document.querySelectorAll('[data-cell]').forEach((el) => el.addEventListener('click', () => {
    state.cell = Number(el.dataset.cell);
    useGrid(state.cell).catch(showDataError);
  }));
  $('#hour').addEventListener('input', (e) => {
    state.hour = Number(e.target.value);
    render();
  });
  $('#play').addEventListener('click', togglePlay);
  $('#share').addEventListener('click', share);
  $('#explainer-toggle').addEventListener('click', () => $('#explainer').classList.toggle('is-collapsed'));
  const setTab = (view) => document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.view === view));
  $('.tab[data-view="about"]').addEventListener('click', () => {
    setTab('about');
    $('#about').showModal();
  });
  $('#about').addEventListener('close', () => setTab('map'));

  map.on('mousemove', onHover);
  map.on('mouseout', () => { $('#tooltip').hidden = true; });
  map.on('click', onClick);
  map.on('moveend', writeHash);
}

function togglePlay() {
  const button = $('#play');
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
    button.classList.remove('is-playing');
    button.textContent = '▶';
    return;
  }
  button.classList.add('is-playing');
  button.textContent = '❚❚';
  playTimer = setInterval(() => {
    state.hour = state.hour >= 23 ? 0 : state.hour + 1;
    render();
  }, 900);
}

function featureAt(point) {
  const layers = HOVER_LAYERS.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');
  const box = [[point.x - 4, point.y - 4], [point.x + 4, point.y + 4]];
  return map.queryRenderedFeatures(box, { layers })[0];
}

function onHover(e) {
  const feature = featureAt(e.point);
  const tip = $('#tooltip');
  map.getCanvas().style.cursor = feature && feature.layer.id !== 'grid-fill' && feature.layer.id !== 'grid-hatch' ? 'pointer' : '';
  if (!feature) {
    tip.hidden = true;
    return;
  }
  tip.innerHTML = tooltipHtml(feature);
  tip.style.left = `${e.point.x}px`;
  tip.style.top = `${e.point.y}px`;
  tip.hidden = false;
}

function tooltipHtml(feature) {
  const p = feature.properties;
  switch (feature.layer.id) {
    case 'bus-stops':
      return `<b>${p.name}</b><br>公車站牌 · ${p.route_count} 條路線`;
    case 'mrt-stations':
      return `<b>${p.name}</b><br>軌道車站`;
    case 'mrt-lines':
      return `<b>${p.name}</b>`;
    case 'bus-routes':
      return `<b>${p.name}</b><br>平日計畫發車 ${p.weekday_departures ?? '無班表'} 班 · 點選查看`;
    default: {
      const cell = grid.cells[feature.id];
      const [, , stops, subroutes, unscheduled] = cell;
      const when = `${DAY_LABELS[state.day]} ${hourText(state.hour)}`;
      if (!subroutes) return `<b>${when}</b><br>格內沒有公車站牌`;
      const value = cellValue(cell);
      const lines = [
        `<b>${when}</b>`,
        value === null ? '<span class="warn">格內附線都沒有班表（資料缺漏）</span>' : `計畫發車 <b>${format(value)}</b> 班`,
        `站牌 ${stops} 個 · 附線 ${subroutes} 條`,
      ];
      if (value !== null && unscheduled) lines.push(`<span class="warn">${unscheduled} 條附線無班表，數值偏低</span>`);
      return lines.join('<br>');
    }
  }
}

function format(value) {
  return Number.isInteger(value) ? value : value.toFixed(1);
}

let popup;
function onClick(e) {
  const feature = featureAt(e.point);
  popup?.remove();
  map.setFilter('bus-route-highlight', ['==', ['get', 'route_id'], '']);
  if (!feature) return;
  const p = feature.properties;
  let html;
  if (feature.layer.id === 'bus-routes') {
    map.setFilter('bus-route-highlight', ['==', ['get', 'route_id'], p.route_id]);
    html = `<h4>${p.name}</h4>平日計畫發車：${p.weekday_departures ?? '無班表資料'} 班<br><span class="muted">主路線所有附線加總，不分方向</span>`;
  } else if (feature.layer.id === 'bus-stops') {
    const routes = JSON.parse(p.routes);
    html = `<h4>${p.name}</h4>${routes.length} 條路線 · 平日計畫發車 ${p.weekday_departures ?? '—'} 班<div class="routes">${routes.join('、')}</div>`;
  } else {
    return;
  }
  $('#tooltip').hidden = true;
  popup = new maplibregl.Popup({ closeButton: true, offset: 8 }).setLngLat(e.lngLat).setHTML(html).addTo(map);
}

let searchIndex = [];
function buildSearchIndex(data) {
  const point = (f) => f.geometry.coordinates;
  searchIndex = [
    ...data.district_labels.features.map((f) => ({ name: `${f.properties.name}區`, kind: '行政區', at: point(f), zoom: 13 })),
    ...data.mrt_stations.features.map((f) => ({ name: f.properties.name, kind: '軌道車站', at: point(f), zoom: 15.5 })),
    ...data.bus_stops.features.map((f) => ({ name: f.properties.name, kind: `站牌 · ${f.properties.route_count} 線`, at: point(f), zoom: 16.5 })),
  ];
  const input = $('#search');
  const list = $('#search-results');
  let matches = [];
  const choose = (item) => {
    if (!item) return;
    if (item.kind.startsWith('站牌')) state.layers.add('stops');
    render();
    map.flyTo({ center: item.at, zoom: item.zoom });
    input.value = item.name;
    list.hidden = true;
  };
  input.addEventListener('input', () => {
    const q = input.value.trim();
    matches = q ? searchIndex.filter((item) => item.name.includes(q)).slice(0, 12) : [];
    list.innerHTML = matches.map((m, i) => `<li data-i="${i}">${m.name}<small>${m.kind}</small></li>`).join('');
    list.hidden = matches.length === 0;
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') choose(matches[0]);
    if (e.key === 'Escape') list.hidden = true;
  });
  list.addEventListener('mousedown', (e) => {
    const li = e.target.closest('li');
    if (li) choose(matches[Number(li.dataset.i)]);
  });
  input.addEventListener('blur', () => setTimeout(() => { list.hidden = true; }, 150));
}

function fillAbout() {
  const m = grid.metadata;
  const counts = m.status_counts;
  $('#about-body').innerHTML = `
    <p>本頁呈現臺北市公車路網、捷運路網，以及以約 ${grid.cell_m} m 方格彙整的公車計畫班次。重點是看出「哪裡、什麼時段有多少班公車經過」，不是實際載客量。</p>
    <h3>來源</h3>
    <ul>
      <li>公車路線、站牌、班表：${m.source}，來源時間 ${m.source_data_time}</li>
      <li>行政區界：內政部國土測繪中心（map-data/admin）</li>
      <li>捷運路線與車站、道路、水系、綠地：© OpenStreetMap contributors（Geofabrik 快照）</li>
    </ul>
    <h3>網格怎麼算</h3>
    <ul>
      <li>${m.cell}。</li>
      <li>${m.value}。</li>
      <li>平日以週三班表代表，週六、週日分開計算。</li>
    </ul>
    <h3>限制</h3>
    <ul>${m.limitations.map((x) => `<li>${x}</li>`).join('')}</ul>
    <h3>網格統計</h3>
    <p>共 ${grid.cells.length} 格：有班表 ${counts.ok + counts.partial} 格（其中 ${counts.partial} 格有部分附線缺班表）、無站牌 ${counts.no_stop} 格、資料缺漏 ${counts.no_schedule} 格。有班表的附線 ${m.scheduled_subroutes} 條／有站序的附線 ${m.sequence_subroutes} 條。</p>
    <p class="muted">資料建置：${m.built_at}</p>`;
}

function showDataError(error) {
  $('#loading').hidden = true;
  const el = document.createElement('div');
  el.className = 'card';
  el.style.cssText = 'position:absolute;inset:auto 20px 20px auto;padding:16px 20px;max-width:420px;z-index:5';
  el.innerHTML = `<b>找不到地圖資料</b><p class="muted" style="margin-top:6px;font-size:13px">請先在 repo 根目錄執行 <code>uv run scripts/build_web_data.py</code>。<br>${error.message}</p>`;
  $('.stage').append(el);
}

async function share() {
  writeHash();
  const button = $('#share');
  try {
    await navigator.clipboard.writeText(window.location.href);
    button.textContent = '已複製連結';
  } catch {
    button.textContent = '請複製網址列';
  }
  setTimeout(() => { button.textContent = '分享視圖'; }, 1600);
}

function readHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const hour = Number(params.get('h'));
  const center = (params.get('c') || '').split(',').map(Number);
  return {
    day: DAY_LABELS[params.get('day')] ? params.get('day') : 'weekday',
    hour: Number.isInteger(hour) && params.has('h') && hour >= 0 && hour <= ALL_DAY ? hour : 8,
    cell: CELL_SIZES.includes(Number(params.get('cell'))) ? Number(params.get('cell')) : 500,
    layers: new Set(params.has('layers') ? params.get('layers').split(',').filter((l) => LAYER_GROUPS[l]) : ['grid', 'routes', 'mrt']),
    center: center.length === 2 && center.every(Number.isFinite) ? center : [121.545, 25.06],
    zoom: Number(params.get('z')) || 11.6,
  };
}

function writeHash() {
  const c = map.getCenter();
  const params = new URLSearchParams({
    day: state.day,
    h: state.hour,
    cell: state.cell,
    layers: [...state.layers].join(','),
    z: map.getZoom().toFixed(2),
    c: `${c.lng.toFixed(4)},${c.lat.toFixed(4)}`,
  });
  window.history.replaceState(null, '', `#${params}`);
}
