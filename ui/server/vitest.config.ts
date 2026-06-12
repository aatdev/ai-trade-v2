import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: { '@shared': path.resolve(process.cwd(), '../shared') },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
