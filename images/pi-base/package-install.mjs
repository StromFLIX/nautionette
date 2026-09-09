// Fixed broker entrypoint. Never run this in the backend or a user's chat.
import { spawnSync } from 'node:child_process';
import { chownSync, existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = '/artifact';
const source = JSON.parse(process.env.PACKAGE_SOURCE);
const scripts = process.env.PACKAGE_SCRIPTS === 'true';
// Only this empty, newly-created volume is writable. Drop privileges before
// running any third-party installation hooks. No host/application env is passed.
chownSync(root, 10001, 10001);
process.setgroups([]);
process.setgid(10001);
process.setuid(10001);
process.env.HOME = '/tmp';
process.env.npm_config_cache = '/tmp/npm-cache';
process.env.GIT_TERMINAL_PROMPT = '0';
process.env.GIT_CONFIG_NOSYSTEM = '1';
process.env.GIT_CONFIG_GLOBAL = '/dev/null';

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8', timeout: 240000, maxBuffer: 2 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`${command} failed; check package/version, public access and dependencies`);
  return result.stdout.trim();
}
try {
  let path, resolved, integrity = null;
  const npmFlags = ['--no-audit', '--no-fund', '--registry=https://registry.npmjs.org', ...(scripts ? [] : ['--ignore-scripts'])];
  if (source.kind === 'npm') {
    const prefix = join(root, 'npm');
    mkdirSync(prefix);
    run('npm', ['install', '--prefix', prefix, '--save-exact', ...npmFlags, source.spec]);
    path = join(prefix, 'node_modules', source.name);
    const manifest = JSON.parse(readFileSync(join(path, 'package.json'), 'utf8'));
    resolved = `npm:${source.name}@${manifest.version}`;
    const lock = JSON.parse(readFileSync(join(prefix, 'package-lock.json'), 'utf8'));
    integrity = lock.packages?.[`node_modules/${source.name}`]?.integrity || null;
  } else {
    path = join(root, 'package');
    run('git', ['clone', '--no-checkout', '--', source.url, path]);
    run('git', ['checkout', '--detach', source.ref], path);
    resolved = `${source.url}@${run('git', ['rev-parse', 'HEAD'], path)}`;
    if (existsSync(join(path, 'package.json'))) run('npm', ['install', ...npmFlags], path);
  }
  if (!realpathSync(path).startsWith(`${root}/`)) throw new Error('Package root escaped the artifact');
  const manifest = existsSync(join(path, 'package.json')) ? JSON.parse(readFileSync(join(path, 'package.json'), 'utf8')) : {};
  const resources = {};
  for (const kind of ['extensions', 'skills', 'prompts', 'themes']) {
    const patterns = manifest.pi ? manifest.pi[kind] : existsSync(join(path, kind)) ? [kind] : [];
    if (patterns !== undefined && (!Array.isArray(patterns) || patterns.length > 100 || patterns.some(p =>
      typeof p !== 'string' || p.length > 300 || p.startsWith('/') || p.includes('..') || p.includes('\\')))) {
      throw new Error('Unsupported resource manifest');
    }
    resources[kind] = patterns || [];
  }
  if (!Object.values(resources).some(items => items.length)) throw new Error('No Pi resources declared or discovered');
  // No extension is loaded during installation. This is manifest information,
  // not an assertion that the package is configured or RPC-compatible.
  console.log(JSON.stringify({ root: relative(root, path), resolved, integrity, resources }));
} catch (error) {
  // Do not forward arbitrary lifecycle output (which can contain credentials or
  // forge protocol events). The caller receives only our bounded diagnostic.
  console.log(JSON.stringify({ error: error.message.startsWith('npm failed') || error.message.startsWith('git failed')
    ? error.message : 'Package installation or manifest validation failed' }));
  process.exitCode = 1;
}
