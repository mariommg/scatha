/**
 * X access through the bird CLI: the fallback when the built-in client (x.js) fails.
 *
 * This is the shelling-out code Smaug used before the client was vendored, moved here
 * unchanged in behavior. Each function returns what bird printed, parsed.
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';

export function buildBirdEnv(config) {
  const env = { ...process.env };
  if (config.twitter?.authToken) {
    env.AUTH_TOKEN = config.twitter.authToken;
  }
  if (config.twitter?.ct0) {
    env.CT0 = config.twitter.ct0;
  }
  return env;
}

const birdCmd = config => config.birdPath || 'bird';

// Use temp file to work around bird CLI pipe buffering bug
function runToFile(cmd, env, timeout, label) {
  const tmpFile = path.join(os.tmpdir(), `smaug-${label}-${Date.now()}.json`);
  execSync(`${cmd} > "${tmpFile}"`, { timeout, env, shell: true });
  const output = fs.readFileSync(tmpFile, 'utf8');
  fs.unlinkSync(tmpFile);
  return JSON.parse(output);
}

export async function bookmarks(config, { count, all, maxPages, folderId }) {
  const bird = birdCmd(config);
  const folder = folderId ? ` --folder-id ${folderId}` : '';
  const cmd = all
    ? `${bird} bookmarks${folder} --all --max-pages ${maxPages} --json-full`
    : `${bird} bookmarks${folder} -n ${count} --json-full`;
  return runToFile(cmd, buildBirdEnv(config), all ? 180000 : 60000, 'bookmarks');
}

export async function likes(config, { count }) {
  return runToFile(`${birdCmd(config)} likes -n ${count} --json`, buildBirdEnv(config), 60000, 'likes');
}

export async function tweet(config, id) {
  const output = execSync(`${birdCmd(config)} read ${id} --json`, {
    encoding: 'utf8', timeout: 30000, env: buildBirdEnv(config)
  });
  return JSON.parse(output);
}

export async function thread(config, id) {
  const output = execSync(`${birdCmd(config)} thread ${id} --json`, {
    encoding: 'utf8', timeout: 30000, maxBuffer: 20 * 1024 * 1024, env: buildBirdEnv(config)
  });
  return JSON.parse(output);
}

export async function userTweets(config, handle, count) {
  const output = execSync(`${birdCmd(config)} user-tweets ${handle} -n ${count} --json-full`, {
    encoding: 'utf8', timeout: 30000, maxBuffer: 20 * 1024 * 1024, env: buildBirdEnv(config),
    stdio: ['ignore', 'pipe', 'ignore']
  });
  return JSON.parse(output);
}

export async function search(config, query, count) {
  // query is built by the caller from digits-only IDs
  const output = execSync(`${birdCmd(config)} search "${query}" -n ${count} --json`, {
    encoding: 'utf8', timeout: 30000, env: buildBirdEnv(config)
  });
  return JSON.parse(output);
}

export async function whoami(config) {
  const output = execSync(`${birdCmd(config)} whoami --plain`, {
    encoding: 'utf8', timeout: 30000, env: buildBirdEnv(config), stdio: ['ignore', 'pipe', 'pipe']
  });
  const m = output.match(/user:\s*@(\w+)(?:\s*\((.*)\))?/);
  if (!m) throw new Error(`bird whoami gave no user: ${output.trim().slice(0, 200)}`);
  return { username: m[1], name: m[2] || null };
}
