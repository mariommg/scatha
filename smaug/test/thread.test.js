import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import { fetchSelfThread } from '../src/processor.js';
import { x } from '../src/x.js';

// Self-threads with X faked. A thread's opener often carries no marker at all
// ("8 sentences you should carve into your desk:"), so every bookmark is asked about.
describe('fetchSelfThread', () => {
  let realThread, asked;
  const t = (id, username, inReplyToStatusId, text = `post ${id}`) =>
    ({ id, text, author: { username }, inReplyToStatusId });

  beforeEach(() => {
    realThread = x.thread;
    asked = [];
  });
  afterEach(() => { x.thread = realThread; });

  const answer = (tweets) => { x.thread = async (config, id) => { asked.push(id); return tweets; }; };

  test('an opener with no thread marker still gets the rest of its thread', async () => {
    answer([t('10', 'tim', undefined, '8 sentences to carve into your desk:'), t('11', 'tim', '10'), t('12', 'tim', '11')]);
    const thread = await fetchSelfThread({}, { id: '10', text: '8 sentences to carve into your desk:', author: { username: 'tim' } });
    assert.deepStrictEqual(asked, ['10']);
    assert.deepStrictEqual(thread.map(x => x.id), ['10', '11', '12']);
  });

  test("other people's replies, and the author answering them, stay out", async () => {
    answer([t('10', 'tim'), t('11', 'tim', '10'), t('12', 'stranger', '11'), t('13', 'tim', '12'), t('14', 'stranger', '10')]);
    const thread = await fetchSelfThread({}, { id: '10', text: 'hello', author: { username: 'tim' } });
    assert.deepStrictEqual(thread.map(x => x.id), ['10', '11']);
  });

  test('a single post is not a thread, however many replies it has', async () => {
    answer([t('10', 'tim'), t('11', 'stranger', '10'), t('12', 'other', '10')]);
    assert.strictEqual(await fetchSelfThread({}, { id: '10', text: 'a joke', author: { username: 'tim' } }), null);
  });

  test('a bookmark in the middle of a thread gets the whole chain from the root', async () => {
    answer([t('10', 'tim'), t('11', 'tim', '10'), t('12', 'tim', '11')]);
    const thread = await fetchSelfThread({}, { id: '11', conversationId: '10', text: '2/ more', author: { username: 'tim' } });
    assert.deepStrictEqual(thread.map(x => x.id), ['10', '11', '12']);
  });

  test('a post X won\'t show (deleted, protected) is no thread, not a failed bookmark', async () => {
    x.thread = async () => { throw new Error('HTTP 404: tweet not found'); };
    assert.strictEqual(await fetchSelfThread({}, { id: '10', text: 'hello', author: { username: 'tim' } }), null);
  });

  test('a rate limit fails the bookmark, so it is retried next run instead of noted from its first post', async () => {
    x.thread = async () => { throw new Error('HTTP 429: Rate limit exceeded'); };
    await assert.rejects(fetchSelfThread({}, { id: '10', text: 'hello', author: { username: 'tim' } }), /429/);
  });
});
