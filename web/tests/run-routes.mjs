// Use the existing Vite TypeScript pipeline and Node's native test runner.
import { build } from 'vite';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const dir = await mkdtemp(join(tmpdir(), 'bus-route-tests-'));
try {
  const bundle = await build({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false, logLevel: 'error',
    build: { ssr: 'tests/routeEditor.test.ts', write: false, minify: false } });
  const entry = join(dir, 'routes.test.mjs');
  await writeFile(entry, bundle.output.find((o) => o.type === 'chunk' && o.isEntry).code);
  process.exitCode = spawnSync(process.execPath, [entry], { stdio: 'inherit' }).status ?? 1;
} finally { await rm(dir, { recursive: true, force: true }); }
