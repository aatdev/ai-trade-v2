// Test setup file
import { beforeAll, afterAll, afterEach, vi } from 'vitest';

// Mock environment variables for testing
beforeAll(() => {
  process.env.IB_GATEWAY_HOST = 'localhost';
  process.env.IB_GATEWAY_PORT = '5000';
});

// Clean up mocks after each test
afterEach(() => {
  vi.clearAllMocks();
});

// Global cleanup
afterAll(() => {
  vi.restoreAllMocks();
});
