import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const proxy = {
  '/api': {
    target: 'http://127.0.0.1:8000',
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
};

export default defineConfig({ plugins: [react(), tailwindcss()], server: { proxy }, preview: { proxy } });
