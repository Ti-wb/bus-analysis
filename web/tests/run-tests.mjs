// One Vite build and Node's native runner for all suites; no additional test framework.
import { build } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const groups = {
  routes: ['routeEditor', 'mapLoad'], map: ['mapLoad'], focus: ['stationFocus'],
  age: ['age'], regional: ['regionalGrid'], improvements: ['improvements'],
};
const suites = process.argv.length > 2
  ? [...new Set(process.argv.slice(2).flatMap((group) => {
    if (!Object.hasOwn(groups, group)) throw new Error(`Unknown test group: ${group}`);
    return groups[group];
  }))]
  : [...new Set(Object.values(groups).flat())];
const dir = await mkdtemp(join(tmpdir(), 'bus-web-tests-'));
try {
  await build({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, publicDir: false, logLevel: 'error',
    ssr: { noExternal: ['react', 'react-dom'] },
    build: { ssr: true, outDir: dir, minify: false, rollupOptions: {
      input: Object.fromEntries(suites.map((suite) => [suite, fileURLToPath(new URL(`${suite}.test.ts`, import.meta.url))])),
      output: { entryFileNames: '[name].test.mjs', chunkFileNames: '[name]-[hash].mjs' },
    } },
  });
  for (const suite of suites) {
    const result = spawnSync(process.execPath, [join(dir, `${suite}.test.mjs`)], { stdio: 'inherit' });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exitCode = result.status ?? 1;
  }
} finally { await rm(dir, { recursive: true, force: true }); }
