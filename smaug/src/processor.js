/**
 * Bookmark Processor - Fetches and prepares Twitter bookmarks for analysis
 *
 * This handles the mechanical work:
 * - Fetching bookmarks from X (x.js: vendored bird client, bird CLI as fallback)
 * - Expanding t.co links
 * - Extracting content from linked pages (articles, GitHub repos)
 * - Optional: Bypassing paywalls via archive.ph
 *
 * Outputs a JSON bundle for AI analysis (Claude Code, etc.)
 */

import fs from 'fs';
import path from 'path';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc.js';
import timezone from 'dayjs/plugin/timezone.js';
import { Readability } from '@mozilla/readability';
import { parseHTML } from 'linkedom';
import { loadConfig } from './config.js';
import { safeFetch } from './safe-fetch.js';
import { x } from './x.js';

dayjs.extend(utc);
dayjs.extend(timezone);

/**
 * Search for the original tweet that published an X article.
 * Used when bookmarked tweet is a share, not the original.
 */
async function searchForArticleTweet(articleId, config) {
  try {
    // articleId is validated as digits-only by caller's regex
    const parsed = await x.search(config, `url:x.com/i/article/${articleId}`, 5);
    // bird CLI may return array or { tweets: [...] } depending on version
    const results = Array.isArray(parsed) ? parsed : (parsed.tweets || []);
    if (results.length > 0) {
      // Prefer tweets with article metadata (likely the original)
      for (const tweet of results) {
        if (tweet.article?.title || tweet.article?.previewText) {
          return tweet;
        }
      }
      return results[0];
    }
    return null;
  } catch (error) {
    console.log(`  Article search failed: ${error.message}`);
    return null;
  }
}

/**
 * Fetches X article content via bird CLI.
 * Direct HTTP fetch won't work - X articles are JS-rendered SPAs that require
 * the Twitter API (via bird CLI) to get the actual content.
 */
/** The article ID in an X article URL (x.com/i/article/<id>), or null. */
export function xArticleId(url) {
  return url.match(/\/i\/article\/(\d+)/)?.[1] ?? null;
}

/**
 * The X article in a tweet's data, or null if it doesn't hold one. When someone quotes an
 * article, the full text is in quotedTweet, so the longer text wins; metadata (title,
 * previewText) is kept from whichever side has it. Counts as an article with metadata, or
 * with more than 500 characters of text.
 */
export function pickXArticle(tweetData, { articleId, articleUrl, source, tweetId }) {
  let articleContent = tweetData.text || '';
  let articleMeta = tweetData.article || {};
  let actualTweetId = tweetId;

  const quotedTweet = tweetData.quotedTweet;
  if (quotedTweet) {
    const quotedContent = quotedTweet.text || '';
    const quotedMeta = quotedTweet.article || {};

    const mainHasMeta = articleMeta.title || articleMeta.previewText;
    const quotedHasMeta = quotedMeta.title || quotedMeta.previewText;

    if (quotedContent.length > articleContent.length) {
      articleContent = quotedContent;
      actualTweetId = quotedTweet.id || tweetId;
      // Only switch metadata if quoted has it (preserve main's metadata otherwise)
      if (quotedHasMeta) {
        articleMeta = quotedMeta;
      }
    } else if (quotedHasMeta && !mainHasMeta) {
      articleMeta = quotedMeta;
      if (quotedContent.length > 500) {
        articleContent = quotedContent;
        actualTweetId = quotedTweet.id || tweetId;
      }
    }
  }

  const hasArticleMeta = articleMeta.title || articleMeta.previewText;
  const hasArticleContent = articleContent.length > 500;

  if (hasArticleMeta || hasArticleContent) {
    return {
      articleId,
      title: articleMeta.title || null,
      previewText: articleMeta.previewText || null,
      content: articleContent,
      url: articleUrl,
      source,
      sourceTweetId: actualTweetId
    };
  }
  return null;
}

export async function fetchXArticleContent(articleUrl, config, sourceTweetId = null) {
  const articleId = xArticleId(articleUrl);
  if (!articleId) {
    return { error: 'Could not parse X article URL', source: 'x-article' };
  }

  const extractArticle = (tweetData, source, tweetId) =>
    pickXArticle(tweetData, { articleId, articleUrl, source, tweetId });

  // Try the bookmarked tweet first - fastest path when it contains the article directly
  if (sourceTweetId) {
    try {
      const tweetData = await x.tweet(config, sourceTweetId);
      const result = extractArticle(tweetData, 'bird-cli', sourceTweetId);
      if (result) return result;

      console.log(`  Bookmarked tweet is a share, searching for original article tweet...`);
    } catch (error) {
      console.log(`  Bird CLI article fetch failed: ${error.message}`);
    }
  }

  // Bookmarked tweet was a share/retweet - search for the original article tweet
  const originalTweet = await searchForArticleTweet(articleId, config);
  if (originalTweet) {
    // Use search result directly if it has full content (avoids extra API call)
    const searchContent = originalTweet.text || originalTweet.quotedTweet?.text || '';
    if (searchContent.length > 500) {
      const searchResult = extractArticle(originalTweet, 'bird-cli-search', originalTweet.id);
      if (searchResult) {
        console.log(`  Found original article tweet with full content: ${originalTweet.id}`);
        return searchResult;
      }
    }

    // Search result truncated - need full tweet data
    try {
      console.log(`  Found original article tweet: ${originalTweet.id}, fetching full content...`);
      const tweetData = await x.tweet(config, originalTweet.id);
      const readResult = extractArticle(tweetData, 'bird-cli-search-read', originalTweet.id);
      if (readResult) return readResult;
    } catch (error) {
      console.log(`  Could not read original tweet: ${error.message}`);
    }

    const result = extractArticle(originalTweet, 'bird-cli-search', originalTweet.id);
    if (result) {
      console.log(`  Using search result metadata (truncated content)`);
      return result;
    }
  }

  // Last resort: scrape meta tags (can't get full content - X articles require JS to render)
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    
    const response = await safeFetch(articleUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
      },
      signal: controller.signal
    });
    clearTimeout(timeout);
    
    const html = await response.text();
    const titleMatch = html.match(/<title>([^<]+)<\/title>/i);
    const descMatch = html.match(/<meta[^>]*name=["']description["'][^>]*content=["']([^"']+)["']/i) ||
                      html.match(/<meta[^>]*content=["']([^"']+)["'][^>]*name=["']description["']/i);
    const ogTitleMatch = html.match(/<meta[^>]*property=["']og:title["'][^>]*content=["']([^"']+)["']/i) ||
                         html.match(/<meta[^>]*content=["']([^"']+)["'][^>]*property=["']og:title["']/i);
    const ogDescMatch = html.match(/<meta[^>]*property=["']og:description["'][^>]*content=["']([^"']+)["']/i) ||
                        html.match(/<meta[^>]*content=["']([^"']+)["'][^>]*property=["']og:description["']/i);
    
    const authorMatch = html.match(/@(\w+)/);
    
    return {
      articleId,
      title: ogTitleMatch?.[1] || titleMatch?.[1]?.replace(' / X', '').replace(' on X', '') || null,
      description: ogDescMatch?.[1] || descMatch?.[1] || null,
      author: authorMatch?.[1] || null,
      url: articleUrl,
      source: 'x-article-meta',
      note: 'X article content requires JavaScript rendering - metadata only'
    };
  } catch (error) {
    console.log(`  Could not fetch X article metadata: ${error.message}`);
    return {
      articleId,
      url: articleUrl,
      source: 'x-article',
      error: error.message,
      note: 'X article - content extraction failed'
    };
  }
}

// Sites that typically require paywall bypass
const PAYWALL_DOMAINS = [
  'nytimes.com',
  'wsj.com',
  'washingtonpost.com',
  'theatlantic.com',
  'newyorker.com',
  'bloomberg.com',
  'ft.com',
  'economist.com',
  'bostonglobe.com',
  'latimes.com',
  'wired.com'
];

export function loadState(config) {
  try {
    const content = fs.readFileSync(config.stateFile, 'utf8');
    return JSON.parse(content);
  } catch (error) {
    return {
      last_processed_id: null,
      last_check: null,
      last_processing_run: null
    };
  }
}

export function saveState(config, state) {
  const dir = path.dirname(config.stateFile);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(config.stateFile, JSON.stringify(state, null, 2) + '\n');
}

const rawBio = t => t?._raw?.core?.user_results?.result?.legacy?.description || null;

// --json-full adds the whole API response; keep the author's bio and drop the rest
function slimRaw(tweet) {
  const { _raw, ...rest } = tweet;
  return { ...rest, authorBio: rawBio(tweet) };
}

const bioCache = new Map();

/**
 * Profile bios for accounts mentioned in a post, so the classifier knows who
 * they are ("@dexhorthy" -> "building @humanlayer_dev ... ai that works").
 * One X lookup per new handle per run.
 */
export async function fetchMentionBios(config, text, author, max = 4) {
  const handles = [...new Set((text.match(/(?<![\w@])@(\w{1,15})/g) || []).map(h => h.slice(1)))]
    .filter(h => h.toLowerCase() !== String(author).toLowerCase())
    .slice(0, max);
  const out = [];
  for (const h of handles) {
    if (!bioCache.has(h)) {
      try {
        const parsed = await x.userTweets(config, h, 1);
        const first = (Array.isArray(parsed) ? parsed : (parsed.tweets || []))[0];
        bioCache.set(h, rawBio(first));
      } catch {
        bioCache.set(h, null);
      }
    }
    if (bioCache.get(h)) out.push({ handle: h, bio: bioCache.get(h) });
  }
  return out;
}

export async function fetchBookmarks(config, count = 10, options = {}) {
  try {
    // Use --all for large fetches (> 50) or when explicitly requested
    const useAll = options.all || count > 50;
    const folderId = options.folderId;
    // Calculate maxPages from count (X returns ~20 per page)
    const maxPages = useAll ? (options.maxPages || Math.max(Math.ceil(count / 20), 10)) : undefined;

    console.log(`  Fetching bookmarks${folderId ? ` in folder ${folderId}` : ''}: `
      + (useAll ? `all, up to ${maxPages} pages` : `${count}`));

    const parsed = await x.bookmarks(config, { count, all: useAll, maxPages, folderId });
    // Paginated requests return { tweets: [...], nextCursor: ... },
    // non-paginated ones plain arrays. Handle both formats.
    let bookmarks = (Array.isArray(parsed) ? parsed : (parsed.tweets || [])).map(slimRaw);

    // Respect the count parameter - truncate if we fetched more than requested
    // (paginated mode may return more bookmarks than asked for)
    if (bookmarks.length > count) {
      console.log(`  Fetched ${bookmarks.length} bookmarks, limiting to requested ${count}`);
      bookmarks = bookmarks.slice(0, count);
    }

    return bookmarks;
  } catch (error) {
    throw new Error(`Failed to fetch bookmarks: ${error.message}`);
  }
}

export async function fetchLikes(config, count = 10) {
  try {
    const parsed = await x.likes(config, { count });
    // Handle both array and object formats for consistency
    return Array.isArray(parsed) ? parsed : (parsed.tweets || []);
  } catch (error) {
    throw new Error(`Failed to fetch likes: ${error.message}`);
  }
}

export async function fetchFromSource(config, count = 10, options = {}) {
  const source = config.source || 'bookmarks';

  if (source === 'bookmarks') {
    return fetchBookmarks(config, count, options);
  } else if (source === 'likes') {
    return fetchLikes(config, count);
  } else if (source === 'both') {
    const bookmarks = await fetchBookmarks(config, count, options);
    const likes = await fetchLikes(config, count);
    // Merge and dedupe by ID
    const seen = new Set();
    const merged = [];
    for (const item of [...bookmarks, ...likes]) {
      if (!seen.has(item.id)) {
        seen.add(item.id);
        merged.push(item);
      }
    }
    return merged;
  } else {
    throw new Error(`Invalid source: ${source}. Must be 'bookmarks', 'likes', or 'both'.`);
  }
}

/**
 * Fetch bookmarks from configured folders, tagging each with its folder name
 */
export async function fetchFromFolders(config, count = 10, options = {}) {
  const folders = config.folders || {};
  const folderIds = Object.keys(folders);

  if (folderIds.length === 0) {
    return [];
  }

  console.log(`Fetching from ${folderIds.length} configured folder(s)...`);

  const allBookmarks = [];
  const seen = new Set();

  for (const folderId of folderIds) {
    const folderTag = folders[folderId];
    console.log(`\n📁 Folder "${folderTag}" (${folderId}):`);

    try {
      const bookmarks = await fetchBookmarks(config, count, { ...options, folderId });
      let added = 0;

      for (const bookmark of bookmarks) {
        if (!seen.has(bookmark.id)) {
          seen.add(bookmark.id);
          // Add folder tag to the bookmark
          bookmark._folderTag = folderTag;
          bookmark._folderId = folderId;
          allBookmarks.push(bookmark);
          added++;
        }
      }

      console.log(`  Found ${bookmarks.length} bookmarks, ${added} new`);
    } catch (error) {
      console.error(`  Error fetching folder ${folderId}: ${error.message}`);
    }
  }

  return allBookmarks;
}

/**
 * Fetch the author's own continuation of a thread (self-replies only, no other repliers).
 * Returns tweets in thread order, including the bookmarked one, or null if not a thread.
 * Every bookmark is asked about: openers often carry no marker ("8 sentences to carve into
 * your desk:"), and guessing from the text missed 4 of 7 threads in a sample (2026-10-15).
 */
export async function fetchSelfThread(config, bookmark) {
  try {
    const parsed = await x.thread(config, bookmark.id);
    const tweets = Array.isArray(parsed) ? parsed : (parsed.tweets || []);
    const author = bookmark.author?.username;

    // Walk the same-author reply chain from the conversation root
    const byId = new Map(tweets.map(t => [String(t.id), t]));
    const chain = new Set();
    const rootId = String(bookmark.conversationId || bookmark.id);
    if (byId.get(rootId)?.author?.username === author) chain.add(rootId);
    else chain.add(String(bookmark.id)); // author's chain hangs off someone else's tweet
    let grew = true;
    while (grew) {
      grew = false;
      for (const t of tweets) {
        const id = String(t.id);
        if (!chain.has(id) && t.author?.username === author && chain.has(String(t.inReplyToStatusId))) {
          chain.add(id);
          grew = true;
        }
      }
    }
    const ordered = [...chain].map(id => byId.get(id)).filter(Boolean).sort((a, b) => (BigInt(a.id) < BigInt(b.id) ? -1 : 1));
    return ordered.length > 1 ? ordered : null;
  } catch (error) {
    console.log(`  Could not fetch thread for ${bookmark.id}: ${error.message}`);
    return null;
  }
}

export async function fetchTweet(config, tweetId) {
  try {
    return await x.tweet(config, tweetId);
  } catch (error) {
    console.log(`  Could not fetch parent tweet ${tweetId}: ${error.message}`);
    return null;
  }
}

export async function expandTcoLink(url, timeout = 10000) {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    const response = await safeFetch(url, {
      method: 'HEAD',
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    return response.url || url;
  } catch (error) {
    console.error(`Failed to expand ${url}: ${error.message}`);
    return url;
  }
}

export function isPaywalled(url) {
  // Match the site's host, not the text anywhere in the URL: "ft.com" is inside "microsoft.com"
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return false;
  }
  return PAYWALL_DOMAINS.some(domain => host === domain || host.endsWith(`.${domain}`));
}

export function stripQuerystring(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return url;
  }
}

function extractGitHubInfo(url) {
  const match = url.match(/github\.com\/([^\/]+)\/([^\/]+)/);
  if (!match) return null;
  return { owner: match[1], repo: match[2].replace(/\.git$/, '') };
}

export async function fetchGitHubContent(url) {
  const info = extractGitHubInfo(url);
  if (!info) {
    throw new Error('Could not parse GitHub URL');
  }

  const { owner, repo } = info;

  try {
    const apiUrl = `https://api.github.com/repos/${owner}/${repo}`;
    const repoResponse = await fetch(apiUrl, {
      headers: { 'Accept': 'application/vnd.github.v3+json' }
    });
    const repoJson = await repoResponse.json();

    // Fetch README content
    let readme = '';
    try {
      const readmeUrl = `https://api.github.com/repos/${owner}/${repo}/readme`;
      const readmeResponse = await fetch(readmeUrl, {
        headers: { 'Accept': 'application/vnd.github.v3+json' }
      });
      const readmeJson = await readmeResponse.json();
      if (readmeJson.content) {
        readme = Buffer.from(readmeJson.content, 'base64').toString('utf8');
        if (readme.length > 5000) {
          readme = readme.slice(0, 5000) + '\n...[truncated]';
        }
      }
    } catch (e) {
      console.log(`  No README found for ${owner}/${repo}`);
    }

    return {
      name: repoJson.name,
      fullName: repoJson.full_name,
      description: repoJson.description || '',
      stars: repoJson.stargazers_count,
      language: repoJson.language,
      topics: repoJson.topics || [],
      readme,
      url: repoJson.html_url
    };
  } catch (error) {
    console.error(`  GitHub API error for ${owner}/${repo}: ${error.message}`);
    throw error;
  }
}

function metaContent(document, ...keys) {
  for (const key of keys) {
    const el = document.querySelector(`meta[property="${key}"], meta[name="${key}"]`);
    const v = el?.getAttribute('content')?.trim();
    if (v) return v;
  }
  return null;
}

const BLOCKS = 'p, div, section, article, header, footer, aside, main, nav, h1, h2, h3, h4, h5, h6, ' +
  'li, ul, ol, dl, dt, dd, pre, blockquote, figure, figcaption, table, tr, hr, details, summary';

/**
 * The text of an HTML fragment with a blank line after every block element and a line
 * break for every <br>. textContent alone joins blocks with nothing between them, so a
 * paragraph after a code block or heading ran into it ("...NA" + "The" -> "NAThe").
 */
function blockText(html) {
  const { document } = parseHTML(`<!doctype html><html><body>${html}</body></html>`);
  for (const br of document.querySelectorAll('br')) br.replaceWith(document.createTextNode('\n'));
  for (const el of document.querySelectorAll(BLOCKS)) el.appendChild(document.createTextNode('\n\n'));
  return document.body.textContent;
}

/**
 * HTML -> readable article (Firefox Reader View's algorithm), plus the page's
 * public title and summary, which survive even when the body is paywalled.
 */
export function extractReadable(html) {
  const { document } = parseHTML(html);
  const title = metaContent(document, 'og:title', 'twitter:title') || document.querySelector('title')?.textContent?.trim() || null;
  const description = metaContent(document, 'og:description', 'twitter:description', 'description');
  let text = null, byline = null;
  try {
    const article = new Readability(document).parse();
    const raw = article?.content ? blockText(article.content) : article?.textContent;
    text = raw?.replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n\n').trim() || null;
    byline = article?.byline || null;
  } catch {}
  return { title, description, byline, text };
}

async function fetchHtml(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await safeFetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36' },
      signal: controller.signal
    });
    return (await response.text()).slice(0, 2_000_000);
  } finally {
    clearTimeout(timeout);
  }
}

export async function fetchArticleContent(url) {
  const page = extractReadable(await fetchHtml(url));
  const words = page.text ? page.text.split(/\s+/).length : 0;
  // Little readable text usually means a paywall or a JS-only page
  return { ...page, source: 'direct', paywalled: words < 150 };
}

export async function fetchContent(url, type, config) {
  // Use GitHub API for GitHub URLs
  if (type === 'github') {
    try {
      const ghContent = await fetchGitHubContent(url);
      return { ...ghContent, source: 'github-api' };
    } catch (error) {
      console.log(`  GitHub API failed: ${error.message}`);
    }
  }

  // For paywalled sites, note for manual handling or custom bypass
  if (isPaywalled(url)) {
    console.log(`  Paywalled domain detected: ${url}`);
    try {
      const { title, description, byline } = extractReadable(await fetchHtml(url));
      return { url, title, description, byline, source: 'paywalled', paywalled: true };
    } catch {
      return { url, source: 'paywalled', paywalled: true };
    }
  }

  // Try direct fetch for other URLs
  return await fetchArticleContent(url);
}

/** t.co links in a post's text, then any new ones in its quoted tweet (the bookmarks API truncates it). */
export function tcoLinksOf(text, quotedText) {
  const links = (text || '').match(/https?:\/\/t\.co\/\w+/g) || [];
  for (const link of (quotedText || '').match(/https?:\/\/t\.co\/\w+/g) || []) {
    if (!links.includes(link)) links.push(link);
  }
  return links;
}

/** True if host is one of the domains or a subdomain of one. */
function onSite(host, ...domains) {
  return domains.some(domain => host === domain || host.endsWith(`.${domain}`));
}

/**
 * What an expanded link points at, judged by its site, not by text anywhere in the URL
 * (cmux.com and dropbox.com contain "x.com"). Order matters: X article URLs are on x.com too.
 */
export function linkType(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return 'article';
  }
  const host = u.hostname.toLowerCase();
  if (onSite(host, 'github.com')) return 'github';
  if (onSite(host, 'youtube.com', 'youtu.be')) return 'video';
  if (onSite(host, 'x.com', 'twitter.com')) {
    if (u.pathname.includes('/i/article/')) return 'x-article';
    return u.pathname.includes('/photo/') || u.pathname.includes('/video/') ? 'media' : 'tweet';
  }
  if (url.match(/\.(jpg|jpeg|png|gif|webp)$/i)) return 'image';
  return 'article';
}

/** Whether a tweet's own article metadata should be fetched: it has some, and no t.co link found one already. */
export function wantsDirectArticle(articleMeta, links) {
  return !!articleMeta && !links.some(l => l.type === 'x-article');
}

/** The URL for an article known only from a tweet's metadata. */
export function directArticleUrl(articleMeta, tweetId) {
  return `https://x.com/i/article/${articleMeta.id || tweetId}`;
}

export function getExistingBookmarkIds(config) {
  try {
    const content = fs.readFileSync(config.archiveFile, 'utf8');
    const matches = content.matchAll(/x\.com\/\w+\/status\/(\d+)/g);
    return new Set([...matches].map(m => m[1]));
  } catch {
    return new Set();
  }
}

export async function fetchAndPrepareBookmarks(options = {}) {
  const config = loadConfig(options.configPath);
  const now = dayjs().tz(config.timezone || 'America/New_York');
  console.log(`[${now.format()}] Fetching and preparing bookmarks...`);

  const state = loadState(config);
  const source = options.source || config.source || 'bookmarks';
  const includeMedia = options.includeMedia ?? config.includeMedia ?? false;
  const configWithOptions = { ...config, source, includeMedia };
  const count = options.count || 20;

  // Build fetch options for pagination
  const fetchOptions = {
    all: options.all || count > 50,
    maxPages: options.maxPages
  };

  let tweets = [];
  const hasFolders = Object.keys(config.folders || {}).length > 0;

  if (hasFolders && source === 'bookmarks') {
    // Fetch from each configured folder with tags
    console.log(`Fetching from ${Object.keys(config.folders).length} folder(s)${includeMedia ? ' (with media)' : ''}`);
    tweets = await fetchFromFolders(configWithOptions, count, fetchOptions);
  } else {
    // Normal fetch from source
    console.log(`Fetching from source: ${source}${includeMedia ? ' (with media)' : ''}${fetchOptions.all ? ' (paginated)' : ''}`);
    tweets = await fetchFromSource(configWithOptions, count, fetchOptions);
  }

  if (!tweets || tweets.length === 0) {
    console.log(`No ${source} found`);
    return { bookmarks: [], count: 0 };
  }

  // Get IDs already processed or pending
  const existingIds = getExistingBookmarkIds(config);
  let pendingIds = new Set();
  try {
    if (fs.existsSync(config.pendingFile)) {
      const pending = JSON.parse(fs.readFileSync(config.pendingFile, 'utf8'));
      pendingIds = new Set((pending.bookmarks || []).map(b => b.id.toString()));
    }
  } catch (e) {}

  // Determine which tweets to process
  let toProcess;
  if (options.specificIds) {
    toProcess = tweets.filter(b => options.specificIds.includes(b.id.toString()));
  } else if (options.force) {
    // Force mode: skip duplicate checking, process all fetched tweets
    toProcess = tweets;
  } else {
    toProcess = tweets.filter(b => {
      const id = b.id.toString();
      return !existingIds.has(id) && !pendingIds.has(id);
    });
  }

  // Cap after deduping, so each run takes the newest N not yet processed (backlog in batches)
  if (options.limit && toProcess.length > options.limit) {
    console.log(`Limiting to ${options.limit} of ${toProcess.length} unprocessed tweets`);
    toProcess = toProcess.slice(0, options.limit);
  }

  if (toProcess.length === 0) {
    console.log('No new tweets to process');
    return { bookmarks: [], count: 0 };
  }

  console.log(`Preparing ${toProcess.length} tweets...`);

  const prepared = [];
  const failed = [];

  for (const bookmark of toProcess) {
    try {
      console.log(`\nProcessing bookmark ${bookmark.id}...`);
      const text = bookmark.text || bookmark.full_text || '';

      // Format date from tweet's createdAt, falling back to current date
      let date;
      if (bookmark.createdAt) {
        const tweetDate = dayjs(bookmark.createdAt).tz(config.timezone || 'America/New_York');
        date = tweetDate.format('dddd, MMMM D, YYYY');
      } else {
        date = now.format('dddd, MMMM D, YYYY');
      }
      const author = bookmark.author?.username || bookmark.user?.screen_name || 'unknown';

      // Self-threads: the payload (and often the link) is in the later tweets
      const thread = await fetchSelfThread(config, bookmark);
      const threadText = thread ? thread.map(t => t.text || '').join('\n\n') : null;
      if (thread) console.log(`  Self-thread: ${thread.length} tweets`);

      const tcoLinks = tcoLinksOf(threadText || text, bookmark.quotedTweet?.text);

      const links = [];

      const expandedResults = await Promise.all(
        tcoLinks.map(async (link) => {
          const expanded = await expandTcoLink(link);
          return { original: link, expanded };
        })
      );

      for (const { original: link, expanded } of expandedResults) {
        console.log(`  Expanded: ${link} -> ${expanded}`);

        const type = linkType(expanded);
        let content = null;

        if (type === 'x-article') {
          console.log(`  X article detected: ${expanded}`);
          try {
            content = await fetchXArticleContent(expanded, config, bookmark.id);
            if (content.content) {
              console.log(`  X article fetched: "${content.title || 'untitled'}" (${content.content.length} chars)`);
            } else if (content.title) {
              console.log(`  X article metadata only: "${content.title}"`);
            } else {
              console.log(`  X article: could not extract content`);
            }
          } catch (error) {
            console.log(`  Could not fetch X article: ${error.message}`);
            content = {
              articleId: xArticleId(expanded) ?? undefined,
              url: expanded,
              source: 'x-article',
              error: error.message
            };
          }
        } else if (type === 'tweet') {
          // Quote tweet - fetch the quoted tweet for context
          const tweetIdMatch = expanded.match(/status\/(\d+)/);
          if (tweetIdMatch) {
            const quotedTweetId = tweetIdMatch[1];
            console.log(`  Quote tweet detected, fetching ${quotedTweetId}...`);
            const quotedTweet = await fetchTweet(config, quotedTweetId);
            if (quotedTweet) {
              content = {
                id: quotedTweet.id,
                author: quotedTweet.author?.username || 'unknown',
                authorName: quotedTweet.author?.name || quotedTweet.author?.username || 'unknown',
                text: quotedTweet.text || quotedTweet.full_text || '',
                tweetUrl: `https://x.com/${quotedTweet.author?.username || 'unknown'}/status/${quotedTweet.id}`,
                source: 'quote-tweet'
              };
            }
          }
        }

        // Fetch content for articles and GitHub repos
        if (type === 'article' || type === 'github') {
          try {
            const fetchResult = await fetchContent(expanded, type, config);

            if (fetchResult.source === 'github-api') {
              content = {
                name: fetchResult.name,
                fullName: fetchResult.fullName,
                description: fetchResult.description,
                stars: fetchResult.stars,
                language: fetchResult.language,
                topics: fetchResult.topics,
                readme: fetchResult.readme,
                url: fetchResult.url,
                source: 'github-api'
              };
              console.log(`  GitHub repo: ${fetchResult.fullName} (${fetchResult.stars} stars)`);
            } else {
              content = {
                title: fetchResult.title,
                description: fetchResult.description,
                byline: fetchResult.byline,
                text: fetchResult.text?.slice(0, 40000),
                source: fetchResult.source,
                paywalled: fetchResult.paywalled
              };
            }
          } catch (error) {
            console.log(`  Could not fetch content: ${error.message}`);
            content = { error: error.message };
          }
        }

        links.push({
          original: link,
          expanded,
          type,
          content
        });
      }

      // Fallback for tweets with article metadata but no t.co link to expand
      const processDirectArticle = async (articleMeta, tweetId, source) => {
        if (!wantsDirectArticle(articleMeta, links)) return;

        console.log(`  Direct X article detected ${source}`);
        const articleUrl = directArticleUrl(articleMeta, tweetId);
        try {
          const content = await fetchXArticleContent(articleUrl, config, tweetId);
          if (content.content) {
            console.log(`  X article fetched: "${content.title || 'untitled'}" (${content.content.length} chars)`);
          } else if (content.title) {
            console.log(`  X article metadata only: "${content.title}"`);
          }
          links.push({ original: articleUrl, expanded: articleUrl, type: 'x-article', content });
        } catch (error) {
          console.log(`  Could not fetch X article: ${error.message}`);
          links.push({
            original: articleUrl,
            expanded: articleUrl,
            type: 'x-article',
            content: {
              articleId: articleMeta.id || tweetId,
              title: articleMeta.title || null,
              previewText: articleMeta.previewText || null,
              url: articleUrl,
              source: `${source.replace(/\s+/g, '-')}-meta`,
              error: error.message
            }
          });
        }
      };

      await processDirectArticle(bookmark.article, bookmark.id, 'on tweet');
      await processDirectArticle(bookmark.quotedTweet?.article, bookmark.quotedTweet?.id, 'on quoted tweet');

      // The bookmarks API truncates quoted tweets; read the quoted tweet in full and
      // check what *it* quotes, where the article often lives (2 links deep)
      let nestedQuote = null;
      if (bookmark.quotedTweet?.id && !links.some(l => l.type === 'x-article')) {
        const q = await fetchTweet(config, bookmark.quotedTweet.id);
        if (q?.quotedTweet) {
          nestedQuote = { author: q.quotedTweet.author?.username || 'unknown', text: q.quotedTweet.text || '' };
          await processDirectArticle(q.quotedTweet.article, q.quotedTweet.id, 'two quotes deep');
        }
      }

      let replyContext = null;
      if (bookmark.inReplyToStatusId) {
        console.log(`  This is a reply to ${bookmark.inReplyToStatusId}, fetching parent...`);
        const parentTweet = await fetchTweet(config, bookmark.inReplyToStatusId);
        if (parentTweet) {
          replyContext = {
            id: parentTweet.id,
            author: parentTweet.author?.username || 'unknown',
            authorName: parentTweet.author?.name || parentTweet.author?.username || 'unknown',
            text: parentTweet.text || parentTweet.full_text || '',
            tweetUrl: `https://x.com/${parentTweet.author?.username || 'unknown'}/status/${parentTweet.id}`
          };
        }
      }

      // Check for native quote tweet
      let quoteContext = null;
      if (bookmark.quotedTweet) {
        const qt = bookmark.quotedTweet;
        quoteContext = {
          id: qt.id,
          author: qt.author?.username || 'unknown',
          authorName: qt.author?.name || qt.author?.username || 'unknown',
          text: qt.text || '',
          tweetUrl: `https://x.com/${qt.author?.username || 'unknown'}/status/${qt.id}`,
          source: 'native-quote'
        };
      }

      // Capture media attachments (photos, videos, GIFs) - EXPERIMENTAL
      // Only included if includeMedia is true (--media flag)
      const media = configWithOptions.includeMedia ? (bookmark.media || []) : [];

      const mentions = await fetchMentionBios(config, threadText || text, author);

      // Build tags array from folder tag (if present)
      const tags = [];
      if (bookmark._folderTag) {
        tags.push(bookmark._folderTag);
      }

      prepared.push({
        id: bookmark.id,
        author,
        authorName: bookmark.author?.name || bookmark.user?.name || author,
        authorBio: bookmark.authorBio || null,
        mentions,
        nestedQuote,
        text,
        threadText,
        tweetUrl: `https://x.com/${author}/status/${bookmark.id}`,
        createdAt: bookmark.createdAt,
        links,
        media,
        tags,
        date,
        isReply: !!bookmark.inReplyToStatusId,
        replyContext,
        isQuote: !!quoteContext,
        quoteContext
      });

      const mediaInfo = media.length > 0 ? ` (${media.length} media)` : '';
      const tagInfo = tags.length > 0 ? ` [${tags.join(', ')}]` : '';
      console.log(`  Prepared: @${author} with ${links.length} links${mediaInfo}${tagInfo}${replyContext ? ' (reply)' : ''}${quoteContext ? ' (quote)' : ''}`);

    } catch (error) {
      console.error(`  Error processing bookmark ${bookmark.id}: ${error.message}`);
      failed.push({ id: bookmark.id, error: error.message });
    }
  }

  // Merge prepared bookmarks into pending file
  let existingPending = { bookmarks: [] };
  try {
    if (fs.existsSync(config.pendingFile)) {
      const parsed = JSON.parse(fs.readFileSync(config.pendingFile, 'utf8'));
      existingPending = { bookmarks: parsed.bookmarks || [], ...parsed };
    }
  } catch (e) {}

  const existingPendingIds = new Set(existingPending.bookmarks.map(b => b.id));
  const newBookmarks = prepared.filter(b => !existingPendingIds.has(b.id));

  // Merge and sort by createdAt ascending (oldest first)
  // This ensures when processed, oldest get added first, newest end up on top
  const allBookmarks = [...existingPending.bookmarks, ...newBookmarks];
  allBookmarks.sort((a, b) => {
    const dateA = a.createdAt ? new Date(a.createdAt).getTime() : 0;
    const dateB = b.createdAt ? new Date(b.createdAt).getTime() : 0;
    return dateA - dateB; // Ascending: oldest first
  });

  const output = {
    generatedAt: now.toISOString(),
    count: allBookmarks.length,
    bookmarks: allBookmarks,
    // This fetch's failures, so the caller can report them. They aren't archived,
    // so the next fetch picks them up again.
    prepareFailed: failed
  };

  const pendingDir = path.dirname(config.pendingFile);
  if (!fs.existsSync(pendingDir)) {
    fs.mkdirSync(pendingDir, { recursive: true });
  }
  fs.writeFileSync(config.pendingFile, JSON.stringify(output, null, 2));
  console.log(`\nMerged ${newBookmarks.length} new bookmarks into ${config.pendingFile} (total: ${output.count})`);
  if (failed.length) console.log(`Could not prepare ${failed.length} of ${toProcess.length} bookmarks`);
  console.log(x.summary());

  // Update state
  state.last_check = now.toISOString();
  saveState(config, state);

  return { bookmarks: prepared, count: prepared.length, failed, pendingFile: config.pendingFile };
}
