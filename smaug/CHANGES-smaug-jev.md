# Changes from upstream Smaug

Vendored from https://github.com/alexknowshtml/smaug at commit
`f3309008f93323db6a7e745262fcdb6927a2efd0` (2026-02-02), MIT license (see `LICENSE`).
The full diff against that commit is in `../smaug-upstream-changes.patch`.

smaug-jev uses Smaug for fetching and preparing bookmarks only. Its Claude Code
processing step (`.claude/commands/process-bookmarks.md`, `bookmarks.md`) is not
used; the router in `../router` sorts and writes notes instead.

## Fixes (worth sending upstream)

- **Article text, not HTML.** `fetchArticleContent` returned the first 50 KB of raw
  page HTML (markup and scripts). It now extracts the readable article with Mozilla
  Readability (via linkedom) and keeps the page's public title and summary.
- **Paywalled pages keep their metadata.** Paywalled domains used to return nothing;
  they now return the public title, summary, and byline when the site serves them.
- **`--media` vs. config.** `fetch` passed `includeMedia: false` whenever the flag was
  absent, overriding `includeMedia: true` in the config file.

## Additions

- **Self-threads.** `fetchSelfThread` collects the author's own continuation of a
  thread ("1/", "🧵", or a mid-thread bookmark) before links are scanned, so links in
  later tweets are found.
- **Quote chains.** When a bookmark quotes a tweet that itself quotes an X article,
  the article is fetched (one level deeper than before).
- **Author bios.** Bookmarks are fetched with `--json-full`; the author's profile bio is
  kept (`authorBio`) and the rest of the raw response dropped.
- **Mentioned-account bios.** Up to four `@handles` per post get their profile bio
  (`mentions`), cached per run.
- **`fetch --limit N`.** Caps the batch *after* deduplication, so each run takes the
  newest N not yet processed.
- **Sample data removed.** The upstream author's own `bookmarks.md` and `knowledge/`
  examples are not included.
