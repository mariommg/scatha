# Vendored: bird's X client

From `@steipete/bird` 0.8.0 (npm), `dist/lib/`, by Peter Steinberger, MIT (see `LICENSE`).
bird's source repository is gone and 0.8.0 is its last release, so the client lives here
where it can be patched.

**Kept:** the read paths Smaug uses (bookmarks and bookmark folders, likes, tweet detail
and threads, user lookup and user tweets, search, current user), plus the runtime
query-ID and feature-flag refresh. **Dropped:** posting, follows, lists, news, media
upload, engagement, home timeline, browser-cookie reading, the CLI, and source maps.
`twitter-client.js` is rewritten to compose only the kept mixins; the other files are
unchanged except for removed `sourceMappingURL` lines.

**Used by** `../../src/x.js`, which returns what the bird CLI printed with `--json`, so
callers and `pending-bookmarks.json` see no difference. It keeps bird's caches next to
Smaug's state (`x-query-ids.json`, `x-features.json`) instead of `~/.config/bird`.

**When X changes something:**
- Query IDs rotate: handled at runtime (`runtime-query-ids.js` re-reads them from X's
  web bundles on a 404). Baked-in fallbacks are in `query-ids.json`.
- New required feature flags: `features.json` and `twitter-client-features.js`.
- Response shape: `twitter-client-utils.js` (`mapTweetResult` and friends).
- Headers or auth: `twitter-client-base.js`. Note `x-client-transaction-id` is random
  hex, not X's real algorithm; if X starts enforcing it, that is the place.

Check a patch with `npm test` (offline parser fixture in `test/fixtures/x-tweet-result.json`)
and, with cookies set, `node src/cli.js whoami`.
