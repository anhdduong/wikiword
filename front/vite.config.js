import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    // Dev mode: proxy API calls to the FastAPI server.
    proxy: { '/lookup': 'http://127.0.0.1:8000' },
  },
});
