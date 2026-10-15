import { randomUUID } from 'node:crypto'
import { closeSync, fsyncSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  acceptanceTrustRegistryPath, runtimeR15TrustSnapshotPath, normalizeTrust,
  assertHistoricalTrustCoverage, loadAcceptanceTrustRegistry,
} from '../agent-desktop/scripts/prepare-runtime.mjs'

/** Appending a new signer never removes or redefines previously registered signers. */
export function mergePublisherTrust(registry, input) {
  normalizeTrust(registry, 'acceptance')
  normalizeTrust(input, 'acceptance')
  const existing = new Map(registry.roots.map(root => [root.key_id, root]))
  const appended = []
  for (const root of input.roots) {
    const previous = existing.get(root.key_id)
    if (previous) {
      assertHistoricalTrustCoverage({ ...registry, roots: [previous] }, { ...input, roots: [root] })
    } else appended.push(root)
  }
  return { ...registry, roots: [...registry.roots, ...appended] }
}

export function registerPublisherTrust(input, registryPath = acceptanceTrustRegistryPath, snapshotPath = runtimeR15TrustSnapshotPath) {
  // Fail invalid/private input before obtaining a lock or touching registry bytes.
  normalizeTrust(input, 'acceptance')
  const lock = registryPath + '.lock'
  let lockFd
  try { lockFd = openSync(lock, 'wx') } catch (error) {
    if (error.code === 'EEXIST') throw new Error('Publisher trust registration is already locked; retry after the other registration finishes')
    throw error
  }
  const temporary = registryPath + '.' + randomUUID() + '.tmp'
  try {
    const registry = loadAcceptanceTrustRegistry(registryPath, snapshotPath)
    const merged = mergePublisherTrust(registry, input)
    if (merged.roots.length !== registry.roots.length) {
      const fd = openSync(temporary, 'wx')
      try {
        writeFileSync(fd, JSON.stringify(merged, null, 2) + '\n')
        fsyncSync(fd)
      } finally { closeSync(fd) }
      // Same-directory replacement is atomic; interruption leaves the old complete file.
      renameSync(temporary, registryPath)
    }
    return merged
  } finally {
    rmSync(temporary, { force: true })
    closeSync(lockFd)
    rmSync(lock)
  }
}

export function main(argv) {
  if (argv.length !== 2 || argv[0] !== '--input' || !argv[1] || argv[1].startsWith('--')) throw new Error('Use register-publisher-trust.mjs --input <wrapped-acceptance-trust.json>')
  let input
  try { input = JSON.parse(readFileSync(path.resolve(argv[1]), 'utf8')) } catch {
    throw new Error('Cannot read valid publisher trust JSON input')
  }
  const registered = registerPublisherTrust(input)
  console.log(JSON.stringify({
    root_count: registered.roots.length,
    key_ids: registered.roots.map(root => /^[a-zA-Z0-9_.-]{1,120}$/.test(root.key_id) ? root.key_id : '[non-displayable-key-id]'),
  }))
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)) } catch (error) {
    console.error('Publisher trust registration failed:', error.message)
    process.exitCode = 1
  }
}
