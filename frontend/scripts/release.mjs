#!/usr/bin/env node
/* Build then publish, in one command.

   npm run release -- 0.2.0

   The version is written into tauri.conf.json first, so a release needs nothing
   but the number. Without an argument the current version is rebuilt and
   republished, which is the way to retry a failed upload.

   Signing and the GitHub token are picked up from the environment, falling back
   to .tauri-keys/ (gitignored) so the command stays short:
     .tauri-keys/pebble.key        -> TAURI_SIGNING_PRIVATE_KEY
     .tauri-keys/password.txt      -> TAURI_SIGNING_PRIVATE_KEY_PASSWORD
     .tauri-keys/github-token.txt  -> GITHUB_TOKEN

   The private key is the one secret that cannot be replaced: losing it means no
   further update can be signed for the copies already installed. */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '..')
const keyDir = path.join(frontend, '.tauri-keys')
const keyPath = path.join(keyDir, 'pebble.key')
const tokenPath = path.join(keyDir, 'github-token.txt')
const confPath = path.join(frontend, 'src-tauri', 'tauri.conf.json')
const isWindows = process.platform === 'win32'

const args = process.argv.slice(2).filter(a => a !== '--version' && a !== '-v')
const asked = args.find(a => /^\d+\.\d+\.\d+$/.test(a))
if (asked) {
  const text = await readFile(confPath, 'utf8')
  const next = text.replace(/("version"\s*:\s*")[^"]+(")/, `$1${asked}$2`)
  if (next === text) {
    console.error('tauri.conf.json has no "version" field to update')
    process.exit(1)
  }
  await writeFile(confPath, next, 'utf8')
  console.log(`version -> ${asked}`)
}

if (!process.env.TAURI_SIGNING_PRIVATE_KEY && existsSync(keyPath)) {
  process.env.TAURI_SIGNING_PRIVATE_KEY = keyPath
  console.log(`signing key -> ${keyPath}`)
}
/* The key carries a password, and tauri build stops to ask for it mid-run. */
const passwordPath = path.join(keyDir, 'password.txt')
if (!process.env.TAURI_SIGNING_PRIVATE_KEY_PASSWORD && existsSync(passwordPath)) {
  process.env.TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (await readFile(passwordPath, 'utf8')).trim()
  console.log('key password -> .tauri-keys/password.txt')
}
if (!process.env.TAURI_SIGNING_PRIVATE_KEY) {
  console.error('No signing key: set TAURI_SIGNING_PRIVATE_KEY or put the key back at .tauri-keys/pebble.key')
  process.exit(1)
}

if (!process.env.GITHUB_TOKEN && existsSync(tokenPath)) {
  process.env.GITHUB_TOKEN = (await readFile(tokenPath, 'utf8')).trim()
  console.log('github token -> .tauri-keys/github-token.txt')
}
if (!process.env.GITHUB_TOKEN) {
  console.error('No GitHub token: set GITHUB_TOKEN or save one at .tauri-keys/github-token.txt')
  process.exit(1)
}

/** Inherit stdio so the build's own progress stays visible. `npx` is a .cmd shim
    and needs a shell on Windows, but process.execPath is a real binary whose
    path contains a space ("C:\Program Files\nodejs\node.exe") — letting a shell
    parse that splits it at the space. */
function run(command, argv, useShell = isWindows) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, argv, { cwd: frontend, stdio: 'inherit', shell: useShell })
    child.on('error', reject)
    child.on('exit', code => (code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`))))
  })
}

await run('npx', ['tauri', 'build'])
await run(process.execPath, [path.join(here, 'publish.mjs')], false)
