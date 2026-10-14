import { test, describe } from 'node:test';
import assert from 'node:assert';
import http from 'node:http';
import { isPublicAddress, checkUrl, safeFetch, BlockedUrlError } from '../src/safe-fetch.js';

// A fake DNS: hostname -> addresses. Nothing here touches the network.
const dnsOf = (table) => async (host) => {
  if (!(host in table)) throw Object.assign(new Error(`getaddrinfo ENOTFOUND ${host}`), { code: 'ENOTFOUND' });
  return table[host].map(address => ({ address, family: address.includes(':') ? 6 : 4 }));
};

describe('isPublicAddress', () => {
  for (const ip of ['127.0.0.1', '127.8.8.8', '10.1.2.3', '172.16.0.1', '172.31.255.255', '192.168.1.1',
    '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1', '255.255.255.255',
    '::1', '::', 'fe80::1', 'fc00::1', 'fd12:3456::1', 'ff02::1', '::ffff:127.0.0.1', '::ffff:7f00:1',
    '::ffff:a9fe:a9fe', '[::1]']) {
    test(`blocks ${ip}`, () => assert.strictEqual(isPublicAddress(ip), false));
  }
  for (const ip of ['1.1.1.1', '8.8.8.8', '172.32.0.1', '100.128.0.1', '2606:4700:4700::1111', '::ffff:1.1.1.1']) {
    test(`allows ${ip}`, () => assert.strictEqual(isPublicAddress(ip), true));
  }
  test('rejects things that are not addresses', () => assert.strictEqual(isPublicAddress('localhost'), false));
});

describe('checkUrl', () => {
  const lookup = dnsOf({ 'example.com': ['93.184.215.14'], 'sneaky.example': ['10.0.0.5'],
    'mixed.example': ['93.184.215.14', '127.0.0.1'], 'v6.example': ['2606:4700:4700::1111'] });

  for (const url of ['file:///etc/passwd', 'ftp://example.com/x', 'data:text/plain,hi', 'javascript:alert(1)', 'not a url']) {
    test(`refuses ${url}`, async () => { await assert.rejects(checkUrl(url, { lookup }), BlockedUrlError); });
  }

  // Node's URL parser turns odd spellings of 127.0.0.1 into the plain form before the check sees them
  for (const url of ['http://127.0.0.1/', 'http://2130706433/', 'http://0x7f.1/', 'http://127.1/',
    'http://[::1]:8080/', 'http://[::ffff:127.0.0.1]/', 'http://169.254.169.254/latest/meta-data/']) {
    test(`refuses ${url}`, async () => { await assert.rejects(checkUrl(url, { lookup }), BlockedUrlError); });
  }

  test('refuses a name that resolves to a private address', async () => {
    await assert.rejects(checkUrl('https://sneaky.example/a', { lookup }), /private address/);
  });

  test('refuses a name with any private address among public ones', async () => {
    await assert.rejects(checkUrl('https://mixed.example/a', { lookup }), BlockedUrlError);
  });

  test('allows public names and addresses', async () => {
    assert.strictEqual((await checkUrl('https://example.com/a', { lookup })).hostname, 'example.com');
    await checkUrl('https://v6.example/', { lookup });
    await checkUrl('http://1.1.1.1/', { lookup });
  });
});

describe('safeFetch redirects', () => {
  const lookup = dnsOf({ 'a.example': ['93.184.215.14'], 'b.example': ['93.184.215.15'], 'evil.example': ['192.168.0.1'] });

  // A fake fetch: url -> redirect target, or a 200 page. Records what was actually requested.
  function site(routes) {
    const requested = [];
    const fetchImpl = async (url, init) => {
      requested.push(url);
      assert.strictEqual(init.redirect, 'manual');
      const to = routes[url];
      return to
        ? { status: 302, url, headers: new Headers({ location: to }) }
        : { status: 200, url, headers: new Headers(), text: async () => `page at ${url}` };
    };
    return { fetchImpl, requested };
  }

  test('follows a public chain and reports where it ended', async () => {
    const { fetchImpl } = site({ 'https://a.example/1': 'https://b.example/2', 'https://b.example/2': '/3' });
    const r = await safeFetch('https://a.example/1', {}, { lookup, fetchImpl });
    assert.strictEqual(r.url, 'https://b.example/3');
    assert.strictEqual(await r.text(), 'page at https://b.example/3');
  });

  test('stops at the hop that points inside, before requesting it', async () => {
    const { fetchImpl, requested } = site({
      'https://a.example/1': 'https://b.example/2',
      'https://b.example/2': 'http://169.254.169.254/latest/meta-data/',
    });
    await assert.rejects(safeFetch('https://a.example/1', {}, { lookup, fetchImpl }), BlockedUrlError);
    assert.deepStrictEqual(requested, ['https://a.example/1', 'https://b.example/2']);
  });

  test('a redirect to a name that resolves inside is refused too', async () => {
    const { fetchImpl, requested } = site({ 'https://a.example/1': 'https://evil.example/x' });
    await assert.rejects(safeFetch('https://a.example/1', {}, { lookup, fetchImpl }), /private address/);
    assert.deepStrictEqual(requested, ['https://a.example/1']);
  });

  test('a redirect to a non-web scheme is refused', async () => {
    const { fetchImpl } = site({ 'https://a.example/1': 'file:///etc/passwd' });
    await assert.rejects(safeFetch('https://a.example/1', {}, { lookup, fetchImpl }), BlockedUrlError);
  });

  test('gives up after 5 redirects', async () => {
    const routes = Object.fromEntries([...Array(7)].map((_, i) => [`https://a.example/${i}`, `https://a.example/${i + 1}`]));
    const { fetchImpl, requested } = site(routes);
    await assert.rejects(safeFetch('https://a.example/0', {}, { lookup, fetchImpl }), /redirects/);
    assert.strictEqual(requested.length, 6);
  });
});

describe('safeFetch against a real local server', () => {
  test('the request is refused before it is sent', async () => {
    let hits = 0;
    const server = http.createServer((req, res) => { hits++; res.end('secret'); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address();
    try {
      await assert.rejects(safeFetch(`http://127.0.0.1:${port}/`), BlockedUrlError);
      await assert.rejects(safeFetch(`http://localhost:${port}/`), BlockedUrlError);  // real DNS: localhost is loopback
      assert.strictEqual(hits, 0);
    } finally {
      server.close();
    }
  });
});

describe('Smaug fetches bookmarked links through the guard', () => {
  test('a link to this machine is never requested', async () => {
    const { expandTcoLink, fetchArticleContent } = await import('../src/processor.js');
    let hits = 0;
    const server = http.createServer((req, res) => { hits++; res.end('<html><body><p>secret</p></body></html>'); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const url = `http://127.0.0.1:${server.address().port}/admin`;
    try {
      assert.strictEqual(await expandTcoLink(url), url);  // refused: the link stays as it was
      await assert.rejects(fetchArticleContent(url), BlockedUrlError);
      assert.strictEqual(hits, 0);
    } finally {
      server.close();
    }
  });
});
