import { test, describe, afterEach } from 'node:test';
import assert from 'node:assert';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createX, isRateLimit } from '../src/x.js';
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
    assert.deepStrictEqual(x.counts, { builtin: 1, fallback: 0, fallbackFailed: 0, bird: 0, rateLimited: 0 });
  });

  test('retries a failed call through bird and counts it', async () => {
    const logged = [];
    const x = createX({ builtin: failing, bird, hasBird: () => true, log: m => logged.push(m) });
    assert.strictEqual((await x.tweet(config, '1')).via, 'bird');
    assert.deepStrictEqual(x.counts, { builtin: 0, fallback: 1, fallbackFailed: 0, bird: 0, rateLimited: 0 });
    assert.match(logged[0], /falling back to bird/);
    assert.match(x.summary(), /0 built-in, 1 bird fallbacks/);
  });

  test('a fallback that fails in bird too is counted, not hidden', async () => {
    // 2026-10-10: two deleted accounts failed both ways and the log still read "0 bird fallbacks"
    const x = createX({ builtin: failing, bird: failing, hasBird: () => true, log: quiet });
    await assert.rejects(x.tweet(config, '1'), /X changed something/);
    assert.deepStrictEqual(x.counts, { builtin: 0, fallback: 1, fallbackFailed: 1, bird: 0, rateLimited: 0 });
    assert.match(x.summary(), /1 bird fallbacks \(1 failed there too\)/);
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
    assert.deepStrictEqual(x.counts, { builtin: 0, fallback: 0, fallbackFailed: 0, bird: 1, rateLimited: 0 });
  });

  test('a rate limit is passed up, not retried through bird (same account, same limit)', async () => {
    // 2026-10-15: a thread backfill hit HTTP 429 after ~290 reads; each fallback doubled the failed calls
    let birdCalls = 0;
    const limited = { tweet: async () => { throw new Error('HTTP 429: Rate limit exceeded'); } };
    const x = createX({ builtin: limited, bird: { tweet: async () => { birdCalls++; } }, hasBird: () => true, log: quiet });
    await assert.rejects(x.tweet(config, '1'), err => isRateLimit(err));
    assert.strictEqual(birdCalls, 0);
    assert.deepStrictEqual(x.counts, { builtin: 0, fallback: 0, fallbackFailed: 0, bird: 0, rateLimited: 1 });
    assert.match(x.summary(), /1 rate-limited/);
  });

  test('isRateLimit knows a 429 from other failures', () => {
    assert.ok(isRateLimit(new Error('HTTP 429: Rate limit exceeded')));
    assert.ok(isRateLimit(new Error('Rate limit exceeded')));
    assert.ok(!isRateLimit(new Error('HTTP 404: tweet not found')));
    assert.ok(!isRateLimit(new Error('X changed something')));
  });
});
