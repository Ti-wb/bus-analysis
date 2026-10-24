import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// Relative base so the build also works from a GitHub Pages sub-path.
export default defineConfig(({ command, mode }) => ({
  base: './',
  plugins: [react()],
  // Appended to data URLs; CI builds use the commit, local builds the build time, the dev server none.
  define: {
    'import.meta.env.VITE_DATA_VERSION': JSON.stringify(command === 'build'
      ? loadEnv(mode, '.', 'GITHUB_').GITHUB_SHA?.slice(0, 12) || Date.now().toString(36) : ''),
  },
  // MapLibre alone is about 1 MB; splitting it gains nothing for a single-page map.
  build: { chunkSizeWarningLimit: 1400 },
}));
