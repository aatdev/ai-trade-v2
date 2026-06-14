// test/flex-query-storage.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'fs/promises';
import os from 'os';
import path from 'path';

// Mock fs and os BEFORE importing the module
vi.mock('fs/promises');
vi.mock('os', () => ({
  default: {
    homedir: vi.fn(() => '/mock/home')
  },
  homedir: vi.fn(() => '/mock/home')
}));

import { FlexQueryStorage, SavedFlexQuery } from '../src/flex-query-storage.js';

describe('FlexQueryStorage', () => {
  let storage: FlexQueryStorage;
  const mockStorageFile = '/mock/path/flex-queries.json';
  const mockHomeDir = '/mock/home';

  beforeEach(() => {
    vi.clearAllMocks();

    // Mock os.homedir
    (os.homedir as any).mockReturnValue(mockHomeDir);

    // Mock fs operations
    (fs.access as any).mockResolvedValue(undefined);
    (fs.mkdir as any).mockResolvedValue(undefined);
    (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));
    (fs.writeFile as any).mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('constructor', () => {
    it('should use default storage file path', () => {
      storage = new FlexQueryStorage();
      const expectedPath = path.join(mockHomeDir, '.interactive-brokers-mcp', 'flex-queries.json');
      expect(storage.getStorageFilePath()).toBe(expectedPath);
    });

    it('should use custom storage file path', () => {
      storage = new FlexQueryStorage(mockStorageFile);
      expect(storage.getStorageFilePath()).toBe(mockStorageFile);
    });
  });

  describe('initialize', () => {
    it('should create directory when it does not exist', async () => {
      // Setup mocks before initialization
      (fs.access as any).mockRejectedValueOnce(new Error('ENOENT')); // Directory doesn't exist
      (fs.readFile as any).mockRejectedValueOnce({ code: 'ENOENT' }); // File doesn't exist

      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();

      // Should create the directory
      expect(fs.mkdir).toHaveBeenCalledWith(
        path.dirname(mockStorageFile),
        { recursive: true }
      );

      // Should NOT write file yet (file is created lazily on first save)
      expect(fs.writeFile).not.toHaveBeenCalled();
    });

    it('should load existing data if file exists', async () => {
      const existingData = {
        queries: [
          {
            id: 'query_1',
            name: 'Test Query',
            queryId: '123456',
            createdAt: '2023-01-01T00:00:00.000Z',
          },
        ],
      };

      // Mock fs.access to succeed (directory exists)
      (fs.access as any).mockResolvedValue(undefined);
      // Mock readFile to return existing data (called twice: once for initialize, once for listQueries)
      (fs.readFile as any).mockResolvedValue(JSON.stringify(existingData));

      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();

      const queries = await storage.listQueries();
      expect(queries).toHaveLength(1);
      expect(queries[0].name).toBe('Test Query');
    });

    it('should handle initialization errors', async () => {
      (fs.access as any).mockRejectedValue(new Error('Permission denied'));
      (fs.mkdir as any).mockRejectedValue(new Error('Cannot create directory'));

      storage = new FlexQueryStorage(mockStorageFile);

      await expect(storage.initialize()).rejects.toThrow();
    });
  });

  describe('listQueries', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should return empty array when no queries', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const queries = await storage.listQueries();
      expect(queries).toEqual([]);
    });

    it('should return all saved queries', async () => {
      const mockQueries = [
        {
          id: 'query_1',
          name: 'Query 1',
          queryId: '123',
          createdAt: '2023-01-01T00:00:00.000Z',
        },
        {
          id: 'query_2',
          name: 'Query 2',
          queryId: '456',
          createdAt: '2023-01-02T00:00:00.000Z',
        },
      ];

      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: mockQueries }));

      const queries = await storage.listQueries();
      expect(queries).toHaveLength(2);
      expect(queries[0].name).toBe('Query 1');
      expect(queries[1].name).toBe('Query 2');
    });
  });

  describe('getQuery', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should get query by internal ID', async () => {
      const mockQueries = [
        {
          id: 'query_1',
          name: 'Test Query',
          queryId: '123456',
          createdAt: '2023-01-01T00:00:00.000Z',
        },
      ];

      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: mockQueries }));

      const query = await storage.getQuery('query_1');
      expect(query).toBeDefined();
      expect(query?.name).toBe('Test Query');
    });

    it('should return null when query not found', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const query = await storage.getQuery('nonexistent');
      expect(query).toBeNull();
    });
  });

  describe('getQueryByQueryId', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should get query by IB query ID', async () => {
      const mockQueries = [
        {
          id: 'query_1',
          name: 'Test Query',
          queryId: '123456',
          createdAt: '2023-01-01T00:00:00.000Z',
        },
      ];

      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: mockQueries }));

      const query = await storage.getQueryByQueryId('123456');
      expect(query).toBeDefined();
      expect(query?.name).toBe('Test Query');
    });

    it('should return null when query not found', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const query = await storage.getQueryByQueryId('nonexistent');
      expect(query).toBeNull();
    });
  });

  describe('getQueryByName', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should get query by name (case insensitive)', async () => {
      const mockQueries = [
        {
          id: 'query_1',
          name: 'Test Query',
          queryId: '123456',
          createdAt: '2023-01-01T00:00:00.000Z',
        },
      ];

      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: mockQueries }));

      const query = await storage.getQueryByName('test query');
      expect(query).toBeDefined();
      expect(query?.name).toBe('Test Query');
    });

    it('should return null when query not found', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const query = await storage.getQueryByName('nonexistent');
      expect(query).toBeNull();
    });
  });

  describe('saveQuery', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should save a new query', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const newQuery = {
        name: 'New Query',
        queryId: '789',
        description: 'Test description',
      };

      const savedQuery = await storage.saveQuery(newQuery);

      expect(savedQuery).toBeDefined();
      expect(savedQuery.id).toBeDefined();
      expect(savedQuery.name).toBe('New Query');
      expect(savedQuery.queryId).toBe('789');
      expect(savedQuery.createdAt).toBeDefined();
      expect(fs.writeFile).toHaveBeenCalled();
    });

    it('should throw error when queryId already exists', async () => {
      const existingQuery = {
        id: 'query_1',
        name: 'Existing Query',
        queryId: '789',
        createdAt: '2023-01-01T00:00:00.000Z',
      };

      (fs.readFile as any).mockResolvedValue(
        JSON.stringify({ queries: [existingQuery] })
      );

      const newQuery = {
        name: 'New Query',
        queryId: '789', // Same as existing
        description: 'Test description',
      };

      await expect(storage.saveQuery(newQuery)).rejects.toThrow(
        'A query with the IB Query ID "789" already exists'
      );
    });

    it('should generate unique ID for each query', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const query1 = await storage.saveQuery({
        name: 'Query 1',
        queryId: '123',
      });

      const query2 = await storage.saveQuery({
        name: 'Query 2',
        queryId: '456',
      });

      expect(query1.id).not.toBe(query2.id);
    });
  });

  describe('updateQuery', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should update query fields', async () => {
      const existingQuery = {
        id: 'query_1',
        name: 'Old Name',
        queryId: '123',
        createdAt: '2023-01-01T00:00:00.000Z',
      };

      (fs.readFile as any).mockResolvedValue(
        JSON.stringify({ queries: [existingQuery] })
      );

      const updated = await storage.updateQuery('query_1', {
        name: 'New Name',
        description: 'Updated description',
      });

      expect(updated.name).toBe('New Name');
      expect(updated.description).toBe('Updated description');
      expect(updated.id).toBe('query_1');
      expect(fs.writeFile).toHaveBeenCalled();
    });

    it('should throw error when query not found', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      await expect(
        storage.updateQuery('nonexistent', { name: 'New Name' })
      ).rejects.toThrow('Query with ID "nonexistent" not found');
    });

    it('should prevent duplicate names', async () => {
      const queries = [
        {
          id: 'query_1',
          name: 'Query 1',
          queryId: '123',
          createdAt: '2023-01-01T00:00:00.000Z',
        },
        {
          id: 'query_2',
          name: 'Query 2',
          queryId: '456',
          createdAt: '2023-01-02T00:00:00.000Z',
        },
      ];

      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries }));

      await expect(
        storage.updateQuery('query_1', { name: 'Query 2' })
      ).rejects.toThrow('A query with the name "Query 2" already exists');
    });
  });

  describe('markQueryUsed', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should update lastUsed timestamp', async () => {
      const existingQuery = {
        id: 'query_1',
        name: 'Test Query',
        queryId: '123',
        createdAt: '2023-01-01T00:00:00.000Z',
      };

      (fs.readFile as any).mockResolvedValue(
        JSON.stringify({ queries: [existingQuery] })
      );

      await storage.markQueryUsed('query_1');

      expect(fs.writeFile).toHaveBeenCalled();
      // Check that the query now has a lastUsed field
      const savedData = JSON.parse((fs.writeFile as any).mock.calls[0][1]);
      expect(savedData.queries[0].lastUsed).toBeDefined();
    });

    it('should not throw error if query not found', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      await expect(storage.markQueryUsed('nonexistent')).resolves.not.toThrow();
    });
  });

  describe('deleteQuery', () => {
    beforeEach(async () => {
      storage = new FlexQueryStorage(mockStorageFile);
      await storage.initialize();
    });

    it('should delete query and return true', async () => {
      const existingQuery = {
        id: 'query_1',
        name: 'Test Query',
        queryId: '123',
        createdAt: '2023-01-01T00:00:00.000Z',
      };

      (fs.readFile as any).mockResolvedValue(
        JSON.stringify({ queries: [existingQuery] })
      );

      const result = await storage.deleteQuery('query_1');

      expect(result).toBe(true);
      expect(fs.writeFile).toHaveBeenCalled();

      // Verify query was removed
      const savedData = JSON.parse((fs.writeFile as any).mock.calls[0][1]);
      expect(savedData.queries).toHaveLength(0);
    });

    it('should return false when query not found', async () => {
      (fs.readFile as any).mockResolvedValue(JSON.stringify({ queries: [] }));

      const result = await storage.deleteQuery('nonexistent');

      expect(result).toBe(false);
    });
  });

  describe('getStorageFilePath', () => {
    it('should return the storage file path', () => {
      storage = new FlexQueryStorage(mockStorageFile);
      expect(storage.getStorageFilePath()).toBe(mockStorageFile);
    });
  });
});
