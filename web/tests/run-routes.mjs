// Use the existing Vite TypeScript pipeline and Node's native test runner.
import { build } from 'vite';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const dir = await mkdtemp(join(tmpdir(), 'bus-route-tests-'));
// Keep recovery regressions in the route test entry point already run by CI.
const suites = process.argv[2]
  ? [({ improvements: 'improvements', regional: 'regionalGrid', map: 'mapLoad' })[process.argv[2]] ?? 'routeEditor']
  : ['routeEditor', 'mapLoad'];
try {
  for (const suite of suites) {
    const bundle = await build({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, logLevel: 'error',
      build: { ssr: `tests/${suite}.test.ts`, write: false, minify: false } });
    const entry = join(dir, 'routes.test.mjs');
    await writeFile(entry, bundle.output.find((o) => o.type === 'chunk' && o.isEntry).code);
    const status = spawnSync(process.execPath, [entry], { stdio: 'inherit' }).status ?? 1;
    if (status) process.exitCode = status;
  }
} finally { await rm(dir, { recursive: true, force: true }); }
