import { defineConfig } from 'vite';

// Relative base so the build also works from a GitHub Pages sub-path.
export default defineConfig({
  base: './',
  // MapLibre alone is about 1 MB; splitting it gains nothing for a single-page map.
  build: { chunkSizeWarningLimit: 1200 },
});
