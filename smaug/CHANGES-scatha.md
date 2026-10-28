# Changes from upstream Smaug

Vendored from https://github.com/alexknowshtml/smaug at commit
`f3309008f93323db6a7e745262fcdb6927a2efd0` (2026-02-02), MIT license (see `LICENSE`).
Upstream has not changed since (checked 2026-10-27). To see every change, compare this
folder with that commit.

scatha uses Smaug for fetching and preparing bookmarks only. Its Claude Code
processing step (`.claude/commands/process-bookmarks.md`, `bookmarks.md`) is not
used; the router in `../router` sorts and writes notes instead.

## Fixes (worth sending upstream)

- **Links can't reach this machine's network.** Bookmarked links, and every redirect
  they lead through, go through `src/safe-fetch.js`: web addresses only, and a host
  that resolves to loopback, private, link-local (incl. 169.254.169.254), or another
  non-public range is refused before any request is sent. Redirects are followed one
  hop at a time (5 at most). Used by t.co expansion, article fetches, and the X article
  page fallback; a refused link only loses that link's content. Known gap: DNS
  rebinding (the name is looked up again when fetch connects). No new dependencies.
- **Paywall check matches the site, not text.** `isPaywalled` used to ask whether the URL
  contained a listed domain anywhere, so `microsoft.com` counted as `ft.com` (and a
  domain in a path, query, or lookalike host counted too), and the article body was
  never fetched. It now parses the host and matches the domain or a subdomain of it.
- **Link types by site.** `linkType` judged links by text anywhere in the URL, so
  `cmux.com` and `dropbox.com` (both contain `x.com`) were treated as tweets and their
  pages never fetched; a domain in a path or query counted too. It now parses the
  host and matches the site or a subdomain. Of 466 past links, only `cmux.com` changes.
- **Article text, not HTML.** `fetchArticleContent` returned the first 50 KB of raw
  page HTML (markup and scripts). It now extracts the readable article with Mozilla
  Readability (via linkedom) and keeps the page's public title and summary.
- **Paragraphs stay apart in article text.** Readability's `textContent` joins block
  elements with nothing between them ("...NA" + "The" -> "NAThe"). Article text now
  comes from Readability's HTML with a blank line after each block element and a line
  break for each `<br>`. On six live pages, glued joins fell from 218 to 44 (most of
  the rest are camelCase in code samples), with no text lost.
- **Paywalled pages keep their metadata.** Paywalled domains used to return nothing;
  they now return the public title, summary, and byline when the site serves them.
- **`--media` vs. config.** `fetch` passed `includeMedia: false` whenever the flag was
  absent, overriding `includeMedia: true` in the config file.
- **Failed bookmarks are reported.** A bookmark that throws while being prepared used
  to be logged and dropped from the batch. The pending file now lists them under
  `prepareFailed` (`{id, error}`, this fetch only) and `fetchAndPrepareBookmarks`
  returns them as `failed`, so a caller can tell a partial or failed batch from an
  empty one. They are not archived, so the next fetch picks them up again.

## Additions

- **Self-threads, for every bookmark.** `fetchSelfThread` collects the author's own
  continuation of a thread before links are scanned, so links in later tweets are
  found. Every bookmark is asked about (one X read each): thread openers often carry
  no marker like "1/" or "🧵", and guessing from the text missed 4 of 7 threads in a
  live sample. Single posts return null; other people's replies are left out.
- **A rate limit fails the bookmark.** If X rate-limits the thread read, the bookmark
  goes to `prepareFailed` and is retried next run, instead of being noted from its
  first post as "not a thread". Any other failure (deleted, protected) still means no
  thread. A rate limit isn't retried through bird (same cookies, same limit), and the
  "X client:" summary line counts them.
- **Quote chains.** When a bookmark quotes a tweet that itself quotes an X article,
  the article is fetched (one level deeper than before).
- **Author bios.** Bookmarks are fetched with `--json-full`; the author's profile bio is
  kept (`authorBio`) and the rest of the raw response dropped.
- **Mentioned-account bios.** Up to four `@handles` per post get their profile bio
  (`mentions`), cached per run.
- **`fetch --limit N`.** Caps the batch *after* deduplication, so each run takes the
  newest N not yet processed.
- **Own X client, bird as fallback.** bird's client library (0.8.0, MIT) is vendored in
  `vendor/bird/` and called in-process through `src/x.js`, which returns the same JSON
  the bird CLI printed. If a call fails and the bird CLI is installed, that call is
  retried through it (`src/x-bird.js`, the old shell-out code). `SMAUG_X_CLIENT=builtin|bird`
  forces one path; each fetch ends with a count of built-in calls and fallbacks,
  including fallbacks that failed in bird too.
  `fetchBookmarks`, `fetchTweet`, `fetchSelfThread`, `fetchMentionBios`, `fetchLikes`,
  `fetchFromSource`, and `fetchFromFolders` are now async. New `whoami` command;
  `setup` no longer requires bird.
- **Article detection is testable.** The link-type, t.co, direct-article, and
  quoted-article logic moved out of `fetchAndPrepareBookmarks` (and a closure in
  `fetchXArticleContent`) into six exported functions; the tests that copied that
  logic now call them. Byte-identical output on live bookmarks. Five fixture-only
  tests and their fixtures removed.
- **Sample data removed.** The upstream author's own `bookmarks.md` and `knowledge/`
  examples are not included.
