import { test, describe, afterEach } from 'node:test';
import assert from 'node:assert';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createX } from '../src/x.js';
import { mapTweetResult } from '../vendor/bird/twitter-client-utils.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

describe('vendored X client parser', () => {
  const { raw, expected } = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'x-tweet-result.json'), 'utf8'));

  test('maps a captured TweetDetail result as it did when captured', () => {
    assert.deepStrictEqual(JSON.parse(JSON.stringify(mapTweetResult(raw, {}))), expected);
  });

  test('yields the fields Smaug reads', () => {
    const t = mapTweetResult(raw, { includeRaw: true });
    assert.match(t.id, /^\d+$/);
    assert.ok(t.text);
    assert.ok(t.author?.username);
    assert.ok(t.conversationId);
    assert.ok(t.media?.length && t.media[0].url);
    // processor.js keeps the author's bio from here (rawBio)
    assert.strictEqual(typeof t._raw.core.user_results.result.legacy.description, 'string');
  });
});

describe('X client fallback', () => {
  const config = {};
  const failing = { tweet: async () => { throw new Error('X changed something'); } };
  const working = { tweet: async (_, id) => ({ id, via: 'builtin' }) };
  const bird = { tweet: async (_, id) => ({ id, via: 'bird' }) };
  const quiet = () => {};

  afterEach(() => { delete process.env.SMAUG_X_CLIENT; });

  test('uses the built-in client when it works', async () => {
    const x = createX({ builtin: working, bird, hasBird: () => true, log: quiet });
    assert.strictEqual((await x.tweet(config, '1')).via, 'builtin');
    assert.deepStrictEqual(x.counts, { builtin: 1, fallback: 0, bird: 0 });
  });

  test('retries a failed call through bird and counts it', async () => {
    const logged = [];
    const x = createX({ builtin: failing, bird, hasBird: () => true, log: m => logged.push(m) });
    assert.strictEqual((await x.tweet(config, '1')).via, 'bird');
    assert.deepStrictEqual(x.counts, { builtin: 0, fallback: 1, bird: 0 });
    assert.match(logged[0], /falling back to bird/);
    assert.match(x.summary(), /0 built-in, 1 bird fallbacks/);
  });

  test('without bird, the built-in error surfaces', async () => {
    const x = createX({ builtin: failing, bird, hasBird: () => false, log: quiet });
    await assert.rejects(x.tweet(config, '1'), /X changed something/);
  });

  test('SMAUG_X_CLIENT=builtin never falls back', async () => {
    process.env.SMAUG_X_CLIENT = 'builtin';
    const x = createX({ builtin: failing, bird, hasBird: () => true, log: quiet });
    await assert.rejects(x.tweet(config, '1'), /X changed something/);
  });

  test('SMAUG_X_CLIENT=bird goes straight to bird', async () => {
    process.env.SMAUG_X_CLIENT = 'bird';
    const x = createX({ builtin: working, bird, hasBird: () => true, log: quiet });
    assert.strictEqual((await x.tweet(config, '1')).via, 'bird');
    assert.deepStrictEqual(x.counts, { builtin: 0, fallback: 0, bird: 1 });
  });
});
