// Materialize Settings-managed configuration into this call's ephemeral Pi home.
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, unlinkSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

export function preparePackages({ file = '/tmp/nautionette-packages.json', agentDir = process.env.PI_CODING_AGENT_DIR,
  artifactDir = '/opt/nautionette-packages', inheritedEnv = process.env } = {}) {
  if (!existsSync(file)) return {};
  const items = JSON.parse(readFileSync(file, 'utf8'));
  unlinkSync(file);
  if (!agentDir) throw new Error('Missing managed Pi home');
  mkdirSync(agentDir, { recursive: true });
  const settingsPath = join(agentDir, 'settings.json');
  const settings = existsSync(settingsPath) ? JSON.parse(readFileSync(settingsPath, 'utf8')) : {};
  const sources = [];
  const env = {};
  const paths = new Set();
  for (const item of items) {
    if (!/^[a-f0-9]{32}$/.test(item.installation_id)) throw new Error('Invalid package installation');
    const mount = resolve(artifactDir, item.installation_id);
    const root = realpathSync(join(mount, item.root));
    if (!root.startsWith(`${mount}/`)) throw new Error('Package root escaped its mount');
    sources.push({ source: root, ...item.filters });
    for (const [key, value] of Object.entries(item.configuration.env)) {
      if (Object.hasOwn(inheritedEnv, key) || Object.hasOwn(env, key)) throw new Error('Package environment conflicts with runtime');
      env[key] = value;
    }
    for (const [path, value] of Object.entries(item.configuration.files)) {
      if (!/^agent\/[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\.(json|yaml|yml|toml|txt)$/.test(path)
          || ['agent/settings.json', 'agent/auth.json', 'agent/models.json', 'agent/trust.json'].includes(path)
          || paths.has(path)) throw new Error('Unsupported package configuration path');
      const target = join(agentDir, path.slice('agent/'.length));
      if (existsSync(target) && lstatSync(target).isSymbolicLink()) throw new Error('Configuration symlinks are not allowed');
      writeFileSync(target, value, { mode: 0o600 });
      paths.add(path);
    }
  }
  if (existsSync(settingsPath) && lstatSync(settingsPath).isSymbolicLink()) throw new Error('Pi settings cannot be a symlink');
  // Append managed local sources. Do not replace gateway/internet extensions or
  // allow npm/git fetching at turn startup. Artifact volumes stay read-only.
  settings.packages = [...(settings.packages || []), ...sources];
  writeFileSync(settingsPath, JSON.stringify(settings), { mode: 0o600 });
  return env;
}
