import { build } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Whale Core's strict CSP requires scripts to be served as separate files.
await build({
  configFile: false,
  base: '/',
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist-whale', emptyOutDir: true, assetsInlineLimit: 0 },
});
