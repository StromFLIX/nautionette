import { spawn } from 'node:child_process'
import { setTimeout as sleep } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'

// Only retry transient download failures. Compilation, signing, checksum and
// configuration errors must fail normally, not be hidden by a blanket retry.
export function isTransientDownloadFailure(output) {
  return /Response code (?:429|5\d{2})\b|\b(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|EPIPE)\b/.test(output)
}

export async function packageWithRetry(attempt, { wait = sleep, log = console.warn } = {}) {
  for (let number = 1; number <= 3; number++) {
    const result = await attempt()
    if (result.code === 0) return 0
    if (number === 3 || !isTransientDownloadFailure(result.output)) return result.code || 1
    const delay = number * 30_000
    log(`Transient desktop download failure; retrying packaging in ${delay / 1000}s (${number}/3 attempts used).`)
    await wait(delay)
  }
}

export function runBuild(args) {
  // npm_execpath avoids shell quoting and npm.cmd differences on Windows.
  if (!process.env.npm_execpath) throw new Error('Run this script through npm run desktop:build:ci')
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [process.env.npm_execpath, 'run', 'desktop:build', '--', ...args], {
      stdio: ['inherit', 'pipe', 'pipe'],
    })
    let output = ''
    for (const [source, destination] of [[child.stdout, process.stdout], [child.stderr, process.stderr]]) {
      source.on('data', chunk => {
        destination.write(chunk)
        output = (output + chunk.toString()).slice(-65_536)
      })
    }
    child.once('error', reject)
    child.once('close', (code, signal) => {
      if (signal) reject(new Error(`Desktop packaging terminated by ${signal}`))
      else resolve({ code, output })
    })
  })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await packageWithRetry(() => runBuild(process.argv.slice(2)))
}
