// Shared durability primitive. Keep extension imports independent of the full Pi SDK.
import { closeSync, fsyncSync, openSync } from 'node:fs';

export function syncFile(path) {
  const fd = openSync(path, 'r');
  try { fsyncSync(fd); } finally { closeSync(fd); }
}
