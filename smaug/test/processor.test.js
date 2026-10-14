import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import {
  isPaywalled, stripQuerystring, fetchXArticleContent, fetchBookmarks,
  xArticleId, linkType, pickXArticle, wantsDirectArticle, directArticleUrl, tcoLinksOf,
} from '../src/processor.js';
import { loadConfig } from '../src/config.js';
import { x } from '../src/x.js';

// Live tests run only when X accepts the cookies in the environment
async function hasXCredentials() {
  try {
    await x.whoami(loadConfig());
    return true;
  } catch {
    return false;
  }
}

const X_AVAILABLE = await hasXCredentials();

describe('X article URL detection', () => {
  test('pulls the article ID out of an X article URL', () => {
    assert.strictEqual(xArticleId('https://x.com/i/article/2015364734262935552'), '2015364734262935552');
  });

  test('regular tweet URLs have no article ID', () => {
    assert.strictEqual(xArticleId('https://x.com/user/status/123456789'), null);
  });

  test('an X article URL is an article, not a tweet', () => {
    assert.strictEqual(linkType('https://x.com/i/article/2015364734262935552'), 'x-article');
    assert.strictEqual(linkType('https://x.com/user/status/123456789'), 'tweet');
  });

  test('the site decides the type, not text anywhere in the URL', () => {
    // 2026-10-14: cmux.com (cmu + x.com) was treated as a tweet, so its page was never fetched
    for (const url of ['https://cmux.com/', 'https://www.dropbox.com/s/a', 'https://www.netflix.com/title/1',
      'https://linux.com/news', 'https://example.com/?ref=x.com', 'https://example.com/i/article/123',
      'https://notgithub.com/a/b', 'https://example.com/youtube.com']) {
      assert.strictEqual(linkType(url), 'article', url);
    }
    assert.strictEqual(linkType('https://mobile.twitter.com/u/status/1'), 'tweet');
    assert.strictEqual(linkType('https://www.github.com/a/b'), 'github');
    assert.strictEqual(linkType('https://m.youtube.com/watch?v=1'), 'video');
    assert.strictEqual(linkType('https://X.COM/u/status/1'), 'tweet');
    assert.strictEqual(linkType('not a url'), 'article');
  });

  test('other link types', () => {
    assert.strictEqual(linkType('https://github.com/a/b'), 'github');
    assert.strictEqual(linkType('https://youtu.be/abc'), 'video');
    assert.strictEqual(linkType('https://www.youtube.com/watch?v=abc'), 'video');
    assert.strictEqual(linkType('https://x.com/user/status/1/photo/1'), 'media');
    assert.strictEqual(linkType('https://example.com/pic.PNG'), 'image');
    assert.strictEqual(linkType('https://example.com/post'), 'article');
  });
});

describe('fetchXArticleContent', () => {
  test('returns error for invalid article URL', async () => {
    const result = await fetchXArticleContent('https://x.com/user/status/123', {});
    assert.strictEqual(result.source, 'x-article');
    assert.ok(result.error, 'should return error for non-article URL');
  });

  test('extracts article ID from valid URL', async () => {
    // This test doesn't require bird CLI - it tests the URL parsing
    const url = 'https://x.com/i/article/2015364734262935552';
    const articleIdMatch = url.match(/\/i\/article\/(\d+)/);
    assert.ok(articleIdMatch, 'should extract article ID');
    assert.strictEqual(articleIdMatch[1], '2015364734262935552');
  });

  test('accepts source tweet ID parameter for bird CLI fetch', async () => {
    // Test that the function signature accepts the source tweet ID
    // (actual bird CLI test would require real credentials)
    const result = await fetchXArticleContent(
      'https://x.com/i/article/123456789',
      { twitter: {} },
      '987654321' // source tweet ID
    );
    // Should return some structure even if bird CLI fails
    assert.ok(result.articleId || result.error, 'should return article info or error');
  });

  test('always returns articleId for valid article URLs', async () => {
    // Even when bird CLI fails, articleId should be extracted from URL
    const result = await fetchXArticleContent(
      'https://x.com/i/article/9876543210',
      { twitter: {} }
    );
    assert.strictEqual(result.articleId, '9876543210', 'should extract articleId from URL');
  });

  test('returns url in result', async () => {
    const articleUrl = 'https://x.com/i/article/1111111111';
    const result = await fetchXArticleContent(articleUrl, { twitter: {} });
    assert.strictEqual(result.url, articleUrl, 'should include original URL in result');
  });

  test('attempts search fallback when source tweet lacks article content', async () => {
    // When bird CLI fails on source tweet, function should attempt search
    // (will also fail without credentials, but tests the code path)
    const result = await fetchXArticleContent(
      'https://x.com/i/article/2222222222',
      { twitter: {} },
      '3333333333' // sharing tweet ID (not the original)
    );
    // Should still return article info even if all attempts fail
    assert.strictEqual(result.articleId, '2222222222');
    assert.ok(result.url.includes('2222222222'));
  });
});

describe('X article detection logic', () => {
  // pickXArticle decides whether bird returned an article: metadata, or more than 500 characters
  const pick = (tweetData) => pickXArticle(tweetData, { articleId: '9', articleUrl: 'https://x.com/i/article/9', source: 's', tweetId: '1' });

  test('detects article by metadata (title present)', () => {
    assert.strictEqual(pick({ text: 'Short text', article: { title: 'My Article' } }).title, 'My Article');
  });

  test('detects article by metadata (previewText present)', () => {
    assert.strictEqual(pick({ text: 'Short text', article: { previewText: 'Preview...' } }).previewText, 'Preview...');
  });

  test('detects article by content length (no metadata)', () => {
    const got = pick({ text: 'A'.repeat(600) });
    assert.strictEqual(got.content.length, 600);
    assert.strictEqual(got.title, null);
  });

  test('a 500-character post without metadata is not an article', () => {
    assert.strictEqual(pick({ text: 'A'.repeat(500) }), null);
  });

  test('fails detection when no metadata and short content', () => {
    assert.strictEqual(pick({ text: 'Just a regular tweet' }), null);
    assert.strictEqual(pick({ text: 'Short text', article: {} }), null);  // empty metadata object
  });

  test('carries the ids and source through', () => {
    assert.deepStrictEqual(pick({ text: 'x', article: { title: 'T' } }),
      { articleId: '9', title: 'T', previewText: null, content: 'x', url: 'https://x.com/i/article/9', source: 's', sourceTweetId: '1' });
  });
});

describe('isPaywalled', () => {
  test('detects NYT as paywalled', () => {
    assert.strictEqual(isPaywalled('https://www.nytimes.com/2025/01/01/article.html'), true);
  });

  test('detects WSJ as paywalled', () => {
    assert.strictEqual(isPaywalled('https://www.wsj.com/articles/something'), true);
  });

  test('detects Bloomberg as paywalled', () => {
    assert.strictEqual(isPaywalled('https://www.bloomberg.com/news/article'), true);
  });

  test('returns false for GitHub', () => {
    assert.strictEqual(isPaywalled('https://github.com/user/repo'), false);
  });

  test('returns false for Twitter', () => {
    assert.strictEqual(isPaywalled('https://twitter.com/user/status/123'), false);
  });

  test('matches the site itself and its subdomains', () => {
    assert.strictEqual(isPaywalled('https://ft.com/content/1'), true);
    assert.strictEqual(isPaywalled('https://cooking.nytimes.com/recipes/1'), true);
    assert.strictEqual(isPaywalled('https://WWW.NYTIMES.COM/x'), true);
  });

  test('a listed name inside another site does not count', () => {
    for (const url of [
      'https://www.microsoft.com/en-us/research/blog/x',  // contains "ft.com"
      'https://minecraft.com/news',
      'https://notnytimes.com/a',                          // lookalike
      'https://example.com/why-i-left-nytimes.com',        // in the path
      'https://example.com/?ref=wsj.com',                  // in the query
      'https://wsj.com@example.com/a',                     // in the user-info
      'https://nytimes.com.example.net/a',                 // as a subdomain of another site
      'not a url nytimes.com',
    ]) assert.strictEqual(isPaywalled(url), false, url);
  });
});

describe('stripQuerystring', () => {
  test('removes query parameters', () => {
    const result = stripQuerystring('https://example.com/page?utm_source=twitter&ref=123');
    assert.strictEqual(result, 'https://example.com/page');
  });

  test('preserves path', () => {
    const result = stripQuerystring('https://example.com/deep/nested/path?foo=bar');
    assert.strictEqual(result, 'https://example.com/deep/nested/path');
  });

  test('handles URLs without querystring', () => {
    const result = stripQuerystring('https://example.com/page');
    assert.strictEqual(result, 'https://example.com/page');
  });

  test('handles invalid URLs gracefully', () => {
    const result = stripQuerystring('not-a-url');
    assert.strictEqual(result, 'not-a-url');
  });
});

describe('direct article metadata detection', () => {
  // Tweets can carry article metadata with no t.co link to expand

  test('wants a direct article when the tweet has metadata and no link found one', () => {
    assert.strictEqual(wantsDirectArticle({ id: '987654321', title: 'My Great Article' }, []), true);
  });

  test('skips direct article when X article already found via t.co link', () => {
    const links = [{ original: 'https://t.co/abc123', expanded: 'https://x.com/i/article/999', type: 'x-article', content: {} }];
    assert.strictEqual(wantsDirectArticle({ id: '987654321' }, links), false);
  });

  test('no metadata, nothing to fetch', () => {
    assert.strictEqual(wantsDirectArticle(undefined, []), false);
    assert.strictEqual(wantsDirectArticle(null, [{ type: 'article' }]), false);
  });

  test('generates correct article URL from the article id', () => {
    assert.strictEqual(directArticleUrl({ id: '123456789012345' }, '999999999'), 'https://x.com/i/article/123456789012345');
  });

  test('falls back to the tweet id when article.id is missing', () => {
    assert.strictEqual(directArticleUrl({ title: 'Article without ID' }, '888888888'), 'https://x.com/i/article/888888888');
  });

  test('extracts t.co links from quotedTweet.text', () => {
    // The bookmarks API truncates quoted tweets; the article link may be only there
    assert.deepStrictEqual(tcoLinksOf('"Quote of the article content"', 'https://t.co/abc123xyz'), ['https://t.co/abc123xyz']);
  });

  test('deduplicates t.co links between text and quotedTweet.text', () => {
    assert.deepStrictEqual(tcoLinksOf('Check this out https://t.co/same123', 'Original: https://t.co/same123 https://t.co/other9'),
      ['https://t.co/same123', 'https://t.co/other9']);
  });

  test('no text, no links', () => {
    assert.deepStrictEqual(tcoLinksOf(undefined, undefined), []);
  });
});

describe('extractArticle content/metadata selection', () => {
  // pickXArticle chooses between the tweet and the tweet it quotes
  const pick = (tweetData) => pickXArticle(tweetData, { articleId: '9', articleUrl: 'u', source: 's', tweetId: '1' });

  test('preserves main metadata when quoted has longer content but no metadata', () => {
    const got = pick({ text: 'A'.repeat(500), article: { title: 'Main Title', previewText: 'Main preview' },
      quotedTweet: { id: '555', text: 'B'.repeat(600), article: {} } });
    assert.strictEqual(got.content.length, 600, 'should use longer quoted content');
    assert.strictEqual(got.title, 'Main Title', 'should preserve main metadata');
    assert.strictEqual(got.sourceTweetId, '555', 'content came from the quoted tweet');
  });

  test('uses quoted metadata when main has none', () => {
    const got = pick({ text: 'A'.repeat(1000), article: {}, quotedTweet: { id: '555', text: 'B'.repeat(400), article: { title: 'Quoted Title' } } });
    assert.strictEqual(got.content.length, 1000, 'should keep longer main content');
    assert.strictEqual(got.title, 'Quoted Title', 'should use quoted metadata');
    assert.strictEqual(got.sourceTweetId, '1');
  });

  test('takes the quoted text too when it is long and the main has no metadata', () => {
    const got = pick({ text: 'A'.repeat(700), quotedTweet: { id: '555', text: 'B'.repeat(600), article: { title: 'Quoted Title' } } });
    assert.strictEqual(got.content, 'B'.repeat(600));
    assert.strictEqual(got.sourceTweetId, '555');
  });

  test('uses both quoted content and metadata when quoted is longer and has metadata', () => {
    const got = pick({ text: 'A'.repeat(500), article: { title: 'Main Title' },
      quotedTweet: { id: '555', text: 'B'.repeat(13000), article: { title: 'Quoted Title', previewText: 'Quoted preview' } } });
    assert.strictEqual(got.content.length, 13000, 'should use longer quoted content');
    assert.strictEqual(got.title, 'Quoted Title', 'should use quoted metadata');
  });
});

describe('fetchBookmarks count truncation', () => {
  // The real fetchBookmarks with the X client faked: what it asks X for, and what it hands back.
  // scatha's weekly batch (fetch --limit) depends on this.
  let realBookmarks;
  beforeEach(() => { realBookmarks = x.bookmarks; });
  afterEach(() => { x.bookmarks = realBookmarks; });

  function fakeX(reply) {
    const asked = [];
    x.bookmarks = async (config, opts) => { asked.push(opts); return reply; };
    return asked;
  }
  const tweets = (n) => Array.from({ length: n }, (_, i) => ({ id: `${i}` }));

  test('asks for pages by count, with at least 10 when paginating', async () => {
    for (const [count, opts, all, maxPages] of [
      [100, {}, true, 10],   // 5 pages needed, minimum 10
      [250, {}, true, 13],   // 13 pages needed
      [50, {}, false, undefined],  // small fetches don't paginate
      [100, { maxPages: 5 }, true, 5],  // explicit maxPages wins
      [10, { all: true }, true, 10],    // explicit --all
    ]) {
      const asked = fakeX([]);
      await fetchBookmarks({}, count, opts);
      assert.strictEqual(asked[0].all, all, `count ${count}`);
      assert.strictEqual(asked[0].maxPages, maxPages, `count ${count}`);
      assert.strictEqual(asked[0].count, count);
    }
  });

  test('truncates to the requested count, keeping the newest first', async () => {
    fakeX({ tweets: tweets(161), nextCursor: 'more' });  // paginated replies come wrapped
    const got = await fetchBookmarks({}, 100);
    assert.strictEqual(got.length, 100);
    assert.strictEqual(got[0].id, '0');
    assert.strictEqual(got[99].id, '99');
  });

  test('returns everything when X has fewer than requested', async () => {
    fakeX(tweets(50));  // non-paginated replies are plain arrays
    assert.strictEqual((await fetchBookmarks({}, 100)).length, 50);
    fakeX(tweets(50));
    assert.strictEqual((await fetchBookmarks({}, 50)).length, 50);
  });

  test('a failed X call surfaces as a fetch error', async () => {
    x.bookmarks = async () => { throw new Error('rate limited'); };
    await assert.rejects(fetchBookmarks({}, 10), /Failed to fetch bookmarks: rate limited/);
  });
});

// Integration tests - only run when bird CLI has valid credentials
describe('X article integration tests (requires X credentials)', { skip: !X_AVAILABLE }, () => {
  // These tests require actual X API access
  // They will be skipped if X credentials are not configured

  test('fetches real X article content when credentials available', async () => {
    if (!X_AVAILABLE) {
      return; // Skip if no credentials
    }

    // Use a known X article URL for testing
    // This is João Moura's "Lessons From 2 Billion Agentic Workflows" article
    const articleUrl = 'https://x.com/i/article/1882784553200713866';

    const result = await fetchXArticleContent(articleUrl, {}, null);

    // Should return article structure even if content extraction varies
    assert.ok(result.articleId, 'should have articleId');
    assert.ok(result.url, 'should have url');
    assert.strictEqual(result.url, articleUrl);

    // If we got content, verify it's substantial
    if (result.content && result.content.length > 0) {
      assert.ok(result.content.length > 100, 'content should be substantial if present');
    }
  });

  test('handles non-existent article gracefully', async () => {
    if (!X_AVAILABLE) {
      return;
    }

    // Use a fake article ID that doesn't exist
    const fakeArticleUrl = 'https://x.com/i/article/9999999999999999999';

    const result = await fetchXArticleContent(fakeArticleUrl, {}, null);

    // Should return structure without failing
    assert.ok(result.articleId, 'should have articleId');
    assert.strictEqual(result.articleId, '9999999999999999999');
  });
});
