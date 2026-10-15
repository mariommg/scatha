/**
 * X access for Smaug.
 *
 * Calls go to bird's client library, vendored in vendor/bird/ (MIT) so a change on X's
 * side is something we can patch. If a call fails and the bird CLI is installed, that one
 * call is retried through it (x-bird.js). SMAUG_X_CLIENT=builtin|bird forces one path.
 *
 * Each function returns what the matching bird CLI command printed with --json, parsed,
 * so callers and pending-bookmarks.json don't change.
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import * as birdBackend from './x-bird.js';

// What the CLI printed, as the caller parsed it (drops undefined fields, as JSON.stringify did)
const printed = value => JSON.parse(JSON.stringify(value));

function ok(result) {
  if (!result.success) throw new Error(result.error || 'unknown error');
  return result;
}

let clientPromise = null;

function client(config) {
  clientPromise ??= (async () => {
    // bird's caches default to ~/.config/bird; keep them with Smaug's state.
    // Set before the import: the query-ID store resolves its path when it loads.
    const dir = path.dirname(path.resolve(config.stateFile || '.state/x'));
    process.env.BIRD_QUERY_IDS_CACHE ??= path.join(dir, 'x-query-ids.json');
    process.env.BIRD_FEATURES_CACHE ??= path.join(dir, 'x-features.json');
    const { TwitterClient } = await import('../vendor/bird/twitter-client.js');
    return new TwitterClient({
      // bird read the environment too, so a config without cookies still worked
      cookies: {
        authToken: config.twitter?.authToken || process.env.AUTH_TOKEN,
        ct0: config.twitter?.ct0 || process.env.CT0
      },
      timeoutMs: 30000
    });
  })();
  return clientPromise;
}

const builtinBackend = {
  async bookmarks(config, { count, all, maxPages, folderId, includeRaw = true }) {
    const c = await client(config);
    const r = ok(folderId
      ? (all ? await c.getAllBookmarkFolderTimeline(folderId, { includeRaw, maxPages })
             : await c.getBookmarkFolderTimeline(folderId, count, { includeRaw }))
      : (all ? await c.getAllBookmarks({ includeRaw, maxPages })
             : await c.getBookmarks(count, { includeRaw })));
    const tweets = [...new Map((r.tweets || []).map(t => [t.id, t])).values()];
    return printed(all ? { tweets, nextCursor: r.nextCursor ?? null } : tweets);
  },

  async likes(config, { count }) {
    const c = await client(config);
    return printed(ok(await c.getLikes(count, { includeRaw: false })).tweets || []);
  },

  async tweet(config, id) {
    const c = await client(config);
    const r = ok(await c.getTweet(id, { includeRaw: false }));
    if (!r.tweet) throw new Error(`tweet ${id} not found`);
    return printed(r.tweet);
  },

  async thread(config, id) {
    const c = await client(config);
    return printed(ok(await c.getThread(id, { includeRaw: false })).tweets || []);
  },

  async userTweets(config, handle, count) {
    const c = await client(config);
    const user = ok(await c.getUserIdByUsername(handle));
    if (!user.userId) throw new Error(`could not find user @${handle}`);
    return printed(ok(await c.getUserTweetsPaged(user.userId, count, { includeRaw: true })).tweets || []);
  },

  async search(config, query, count) {
    const c = await client(config);
    return printed(ok(await c.search(query, count, { includeRaw: false })).tweets || []);
  },

  async whoami(config) {
    const c = await client(config);
    const { user } = ok(await c.getCurrentUser());
    if (!user) throw new Error('could not determine the current user');
    return { username: user.username, name: user.name || null };
  }
};

const birdFound = new Map();

function birdInstalled(config) {
  const bird = config.birdPath || 'bird';
  if (!birdFound.has(bird)) {
    let found = fs.existsSync(bird);
    if (!found) {
      try {
        execSync(process.platform === 'win32' ? `where "${bird}"` : `command -v "${bird}"`, { stdio: 'ignore', shell: true });
        found = true;
      } catch {}
    }
    birdFound.set(bird, found);
  }
  return birdFound.get(bird);
}

// X said "slow down". bird reads with the same cookies, so retrying there only doubles the failed calls.
export function isRateLimit(error) {
  return /\b429\b|rate limit/i.test(error?.message || '');
}

export function createX({ builtin = builtinBackend, bird = birdBackend, hasBird = birdInstalled, log = console.log } = {}) {
  const counts = { builtin: 0, fallback: 0, fallbackFailed: 0, bird: 0, rateLimited: 0 };

  async function call(op, config, ...args) {
    const mode = (process.env.SMAUG_X_CLIENT || 'auto').toLowerCase();
    if (mode === 'bird') {
      const out = await bird[op](config, ...args);
      counts.bird++;
      return out;
    }
    try {
      const out = await builtin[op](config, ...args);
      counts.builtin++;
      return out;
    } catch (error) {
      if (isRateLimit(error)) {
        counts.rateLimited++;
        throw error;
      }
      if (mode === 'builtin' || !hasBird(config)) throw error;
      log(`  X: built-in client failed on ${op} (${error.message}); falling back to bird`);
      counts.fallback++;
      try {
        return await bird[op](config, ...args);
      } catch (birdError) {
        counts.fallbackFailed++;
        throw birdError;
      }
    }
  }

  const x = { counts };
  for (const op of Object.keys(builtinBackend)) {
    x[op] = (config, ...args) => call(op, config, ...args);
  }
  x.summary = () => `X client: ${counts.builtin} built-in, ${counts.fallback} bird fallbacks`
    + (counts.fallbackFailed ? ` (${counts.fallbackFailed} failed there too)` : '')
    + (counts.rateLimited ? `, ${counts.rateLimited} rate-limited` : '')
    + (counts.bird ? `, ${counts.bird} through bird (forced)` : '');
  return x;
}

export const x = createX();
