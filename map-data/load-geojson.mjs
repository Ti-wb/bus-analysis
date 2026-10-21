/** Read plain or gzip GeoJSON, including servers that already decoded gzip. */
export async function loadGeoJSON(url, { signal } = {}) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`GeoJSON request failed: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const compressed = bytes[0] === 0x1f && bytes[1] === 0x8b;
  let data;
  if (compressed) {
    if (typeof DecompressionStream === 'undefined') {
      throw new Error('Gzip is not supported by this browser; serve a decompressed GeoJSON file.');
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    data = await new Response(stream).json();
  } else {
    data = JSON.parse(new TextDecoder().decode(bytes));
  }
  if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) {
    throw new Error('Expected a GeoJSON FeatureCollection.');
  }
  return data;
}
