import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fetchAndPrepareBookmarks } from '../src/processor.js';
import { x } from '../src/x.js';

// The real fetchAndPrepareBookmarks with X faked: what lands in the pending file
// when one bookmark of several can't be prepared.
describe('fetchAndPrepareBookmarks with a bookmark that fails to prepare', () => {
  const ENV = ['ARCHIVE_FILE', 'PENDING_FILE', 'STATE_FILE'];
  let dir, saved, realBookmarks;

  const post = (id) => ({ id, text: `post ${id}`, createdAt: '2026-10-01T12:00:00Z', author: { username: 'someone' } });
  // A bookmark whose preparation throws partway (any error inside the per-bookmark try)
  const broken = (id) => ({ ...post(id), author: { get username() { throw new Error('malformed author'); } } });

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'smaug-prepare-'));
    saved = Object.fromEntries(ENV.map(k => [k, process.env[k]]));
    process.env.ARCHIVE_FILE = path.join(dir, 'ingested.md');
    process.env.PENDING_FILE = path.join(dir, 'pending.json');
    process.env.STATE_FILE = path.join(dir, 'state.json');
    realBookmarks = x.bookmarks;
  });

  afterEach(() => {
    x.bookmarks = realBookmarks;
    for (const k of ENV) { if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k]; }
    fs.rmSync(dir, { recursive: true, force: true });
  });

  async function prepare(tweets) {
    x.bookmarks = async () => tweets;
    const configPath = path.join(dir, 'smaug.config.json');
    fs.writeFileSync(configPath, JSON.stringify({ source: 'bookmarks', folders: {} }));
    const result = await fetchAndPrepareBookmarks({ configPath, count: 10 });
    return { result, pending: JSON.parse(fs.readFileSync(process.env.PENDING_FILE, 'utf8')) };
  }

  test('keeps the good ones and records the failure', async () => {
    const { result, pending } = await prepare([post('1'), broken('2'), post('3')]);
    assert.deepStrictEqual(pending.bookmarks.map(b => b.id).sort(), ['1', '3']);
    assert.deepStrictEqual(pending.prepareFailed, [{ id: '2', error: 'malformed author' }]);
    assert.deepStrictEqual(result.failed.map(f => f.id), ['2']);
  });

  test('when every bookmark fails, the pending file says so instead of looking empty', async () => {
    const { pending } = await prepare([broken('2'), broken('3')]);
    assert.strictEqual(pending.bookmarks.length, 0);
    assert.deepStrictEqual(pending.prepareFailed.map(f => f.id), ['2', '3']);
  });

  test('a clean fetch records no failures', async () => {
    const { pending } = await prepare([post('1')]);
    assert.deepStrictEqual(pending.prepareFailed, []);
  });
});
