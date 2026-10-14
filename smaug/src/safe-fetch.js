/**
 * Fetch a link from a bookmark without letting it reach this machine or its network.
 *
 * Bookmarked links (and wherever they redirect) are fetched from the user's own computer.
 * A link that resolves to loopback, a private network, link-local (cloud metadata lives at
 * 169.254.169.254), or another non-public range is refused before any request is sent.
 * Redirects are followed one hop at a time and every hop is checked the same way.
 *
 * Known gap: the address is checked, then fetch() looks the name up again to connect. A DNS
 * server that answers differently the second time (DNS rebinding) could slip past. Closing
 * that means pinning the connection to the checked address, which global fetch can't do
 * without the undici package.
 */

import dns from 'node:dns/promises';
import net from 'node:net';

export class BlockedUrlError extends Error {
  constructor(message) {
    super(message);
    this.name = 'BlockedUrlError';
  }
}

const blocked = new net.BlockList();
for (const [address, prefix] of [
  ['0.0.0.0', 8],        // "this network"
  ['10.0.0.0', 8],       // private
  ['100.64.0.0', 10],    // carrier-grade NAT
  ['127.0.0.0', 8],      // loopback
  ['169.254.0.0', 16],   // link-local, cloud metadata
  ['172.16.0.0', 12],    // private
  ['192.0.0.0', 24],     // protocol assignments
  ['192.0.2.0', 24],     // documentation
  ['192.168.0.0', 16],   // private
  ['198.18.0.0', 15],    // benchmarking
  ['198.51.100.0', 24],  // documentation
  ['203.0.113.0', 24],   // documentation
  ['224.0.0.0', 4],      // multicast
  ['240.0.0.0', 4],      // reserved, broadcast
]) blocked.addSubnet(address, prefix, 'ipv4');
for (const [address, prefix] of [
  ['::', 96],            // unspecified, loopback, old IPv4-compatible form
  ['100::', 64],         // discard
  ['2001:db8::', 32],    // documentation
  ['fc00::', 7],         // unique local (private)
  ['fe80::', 10],        // link-local
  ['ff00::', 8],         // multicast
]) blocked.addSubnet(address, prefix, 'ipv6');

/** The IPv4 address inside an IPv4-mapped IPv6 one (::ffff:127.0.0.1 or ::ffff:7f00:1), else null. */
function mappedIPv4(ip) {
  const m = ip.toLowerCase().match(/^::ffff:(.+)$/);
  if (!m) return null;
  if (net.isIPv4(m[1])) return m[1];
  const groups = m[1].split(':');
  if (groups.length !== 2) return null;
  const n = parseInt(groups[0], 16) * 65536 + parseInt(groups[1], 16);
  return [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join('.');
}

export function isPublicAddress(ip) {
  ip = ip.replace(/^\[|\]$/g, '');
  const family = net.isIP(ip);
  if (family === 4) return !blocked.check(ip, 'ipv4');
  if (family === 6) {
    const v4 = mappedIPv4(ip);
    return v4 ? isPublicAddress(v4) : !blocked.check(ip, 'ipv6');
  }
  return false;
}

/** The parsed URL if it's a web address whose host resolves only to public addresses; throws otherwise. */
export async function checkUrl(url, { lookup = dns.lookup } = {}) {
  let u;
  try {
    u = new URL(url);  // also turns odd spellings like http://2130706433/ into 127.0.0.1
  } catch {
    throw new BlockedUrlError(`not a valid URL: ${url}`);
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    throw new BlockedUrlError(`not a web address (${u.protocol}): ${url}`);
  }
  const host = u.hostname.replace(/^\[|\]$/g, '');
  const addresses = net.isIP(host) ? [host] : (await lookup(host, { all: true, verbatim: true })).map(a => a.address);
  const inside = addresses.find(a => !isPublicAddress(a));
  if (!addresses.length || inside) {
    throw new BlockedUrlError(`skipped ${u.hostname}: it points at a private address (${inside ?? 'none found'})`);
  }
  return u;
}

/**
 * fetch(), with every hop checked by checkUrl. The response's url is where the chain ended.
 * Options: lookup and fetchImpl are seams for tests.
 */
export async function safeFetch(url, init = {}, { lookup, fetchImpl = fetch, maxRedirects = 5 } = {}) {
  let current = url;
  for (let hop = 0; ; hop++) {
    await checkUrl(current, { lookup });
    const response = await fetchImpl(current, { ...init, redirect: 'manual' });
    const location = response.status >= 300 && response.status < 400 && response.headers.get('location');
    if (!location) return response;
    await response.body?.cancel?.();
    if (hop >= maxRedirects) throw new BlockedUrlError(`gave up after ${maxRedirects} redirects from ${url}`);
    current = new URL(location, current).href;
  }
}
