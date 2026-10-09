import { createHash, createPublicKey, verify } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import canonicalizeModule from 'canonicalize'
import { getNodeValue, parseTree, type Node as JsonNode, type ParseError } from 'jsonc-parser'
import semver from 'semver'
import yauzl from 'yauzl'
import { ManagementError } from '../management.js'

export const FIRST_PARTY_PROVIDERS = ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom'] as const
export interface PublisherTrust { key_id: string; public_key: string; providers: string[]; test_only?: boolean }
export function publisherPublicKey(value: string) {
  // createPublicKey also accepts private PEMs; publisher assets must never carry them.
  if (typeof value !== 'string' || !/^-----BEGIN PUBLIC KEY-----\r?\n(?:[A-Za-z0-9+/]{1,64}={0,2}\r?\n)+-----END PUBLIC KEY-----\r?\n?$/.test(value)) throw new Error('Publisher SPKI public key required')
  const key = createPublicKey(value)
  if (key.asymmetricKeyType !== 'ed25519') throw new Error('Publisher Ed25519 public key required')
  return key
}
export interface PackagePlatform { platform: string; arch: string; node_version: string; node_abi: string; runtime_version: string }
export interface RequiredFile { path: string; size: number; sha256: string }
export interface RuntimeManifest {
  manifest_version: 1; provider_id: string; provider_version: string; display_name: string; description: string
  entrypoint: string[]; tools: unknown[]; schema_digest: string; execution_target: string
  distribution: { platform: string; arch: string; node: { version: string; modules_abi: string }; required_files: RequiredFile[] }
  [key: string]: unknown
}
export interface ReleaseEnvelope {
  envelope_version: 1; provider_release_id: string; provider_id: string; provider_version: string
  platform: string; arch: string; package_format: string; package_size: number; package_digest: string
  manifest_digest: string; min_runtime_version: string; max_runtime_version: string | null
  publisher_key_id: string; signature_algorithm: string; signature: string; created_at: string
}
export interface VerifiedPackage { envelope: ReleaseEnvelope; manifest: RuntimeManifest; files: Map<string, Buffer>; outer_digest: string }
export const PACKAGE_LIMITS = { archive: 512 * 1024 ** 2, expanded: 2 * 1024 ** 3, file: 256 * 1024 ** 2, entries: 50_000, ratio: 1000 }
export const sha256 = (value: Buffer | string): string => createHash('sha256').update(value).digest('hex')
function invalid(message = '插件包格式、内容或签名无效'): never { throw new ManagementError(8, message) }
const canonicalize = canonicalizeModule as unknown as (value: unknown) => string | undefined
const wellFormed = (text: string): boolean => !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text)

export function canonicalJson(value: unknown): string {
  const check = (v: unknown, depth: number): void => {
    if (depth > 128) invalid()
    if (typeof v === 'string' && !wellFormed(v)) invalid()
    if (typeof v === 'number' && !Number.isFinite(v)) invalid()
    if (v && typeof v === 'object') for (const [key, child] of Object.entries(v)) { if (!wellFormed(key)) invalid(); check(child, depth + 1) }
  }
  check(value, 0)
  const text = canonicalize(value)
  if (typeof text !== 'string') invalid()
  return text
}
function json(bytes: Buffer): unknown {
  let source: string
  try { source = new TextDecoder('utf-8', { fatal: true }).decode(bytes) } catch { return invalid() }
  const errors: ParseError[] = []
  const tree = parseTree(source, errors, { disallowComments: true, allowTrailingComma: false })
  if (!tree || errors.length) invalid()
  const visit = (node: JsonNode, depth: number): void => {
    if (depth > 128) invalid()
    if (node.type === 'object') {
      const names = new Set<string>()
      for (const property of node.children ?? []) {
        const name = property.children?.[0]?.value as string
        if (names.has(name)) invalid('插件JSON含重复字段')
        names.add(name)
      }
    }
    for (const child of node.children ?? []) visit(child, depth + 1)
  }
  visit(tree, 0)
  const value = getNodeValue(tree); canonicalJson(value); return value
}
export function safePackagePath(name: string): string {
  if (!name || name.length > 230 || name.includes('\\') || name.startsWith('/') || !wellFormed(name)) invalid('插件路径无效')
  const stripped = name.endsWith('/') ? name.slice(0, -1) : name
  for (const part of stripped.split('/')) {
    if (!part || part === '.' || part === '..' || /[\x00-\x1f<>:"|?*]/.test(part) || /[. ]$/.test(part)
      || /^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(part)) invalid('插件路径无效')
  }
  return stripped
}

/** Sequential bounded reads, including inflated bytes. No archive path reaches the OS here. */
export async function readSafeZip(bytes: Buffer, limits = PACKAGE_LIMITS): Promise<Map<string, Buffer>> {
  if (bytes.length > limits.archive) invalid('插件包过大')
  const archive = await new Promise<yauzl.ZipFile>((resolve, reject) => yauzl.fromBuffer(bytes,
    { lazyEntries: true, validateEntrySizes: true, strictFileNames: true }, (error, zip) => error ? reject(error) : resolve(zip!)))
  const files = new Map<string, Buffer>(); const paths = new Map<string, boolean>(); const ancestors = new Set<string>(); let expanded = 0; let count = 0
  try {
    await new Promise<void>((resolve, reject) => {
      let failed = false
      const fail = (error: unknown) => { if (!failed) { failed = true; archive.close(); reject(error) } }
      archive.on('error', fail); archive.on('end', () => resolve())
      archive.on('entry', (entry: yauzl.Entry) => {
        void (async () => {
          const path = safePackagePath(entry.fileName); const dir = entry.fileName.endsWith('/'); const key = path.normalize('NFC').toLowerCase()
          const mode = entry.externalFileAttributes >>> 16
          if (++count > limits.entries || entry.generalPurposeBitFlag & 1 || mode & 0o7000
            || ((mode & 0o170000) !== 0 && (mode & 0o170000) !== (dir ? 0o040000 : 0o100000))
            || paths.has(key) || entry.uncompressedSize > limits.file || (dir && entry.uncompressedSize !== 0)
            || entry.uncompressedSize / Math.max(1, entry.compressedSize) > limits.ratio) invalid('插件ZIP包含不受支持条目')
          for (let prefix = key; prefix.includes('/');) { prefix = prefix.slice(0, prefix.lastIndexOf('/')); if (paths.get(prefix) === false) invalid(); ancestors.add(prefix) }
          if (!dir && ancestors.has(key)) invalid()
          paths.set(key, dir)
          if (dir) { archive.readEntry(); return }
          const stream = await new Promise<NodeJS.ReadableStream>((accept, deny) => archive.openReadStream(entry, (error, value) => error ? deny(error) : accept(value!)))
          const chunks: Buffer[] = []; let size = 0
          for await (const chunk of stream as AsyncIterable<Buffer>) {
            size += chunk.length; expanded += chunk.length
            if (size > limits.file || expanded > limits.expanded) { (stream as import('node:stream').Readable).destroy(); invalid('插件解压体积超限') }
            chunks.push(chunk)
          }
          if (size !== entry.uncompressedSize) invalid()
          files.set(path, Buffer.concat(chunks)); archive.readEntry()
        })().catch(fail)
      })
      archive.readEntry()
    })
  } catch (error) { if (error instanceof ManagementError) throw error; invalid() } finally { archive.close() }
  return files
}

export async function verifyOfflinePackage(bytes: Buffer, trust: PublisherTrust[], platform: PackagePlatform): Promise<VerifiedPackage> {
  try {
    const outer = await readSafeZip(bytes, { ...PACKAGE_LIMITS, entries: 2, expanded: PACKAGE_LIMITS.archive + 65536, file: PACKAGE_LIMITS.archive })
    if (outer.size !== 2 || !outer.has('envelope.json') || !outer.has('payload.zip') || outer.get('envelope.json')!.length > 65536) invalid()
    const envelope = json(outer.get('envelope.json')!) as ReleaseEnvelope
    if (!envelope || typeof envelope !== 'object' || envelope.envelope_version !== 1 || envelope.package_format !== 'zip'
      || envelope.signature_algorithm !== 'Ed25519' || typeof envelope.provider_id !== 'string') invalid()
    if (!(FIRST_PARTY_PROVIDERS as readonly string[]).includes(envelope.provider_id)) throw new ManagementError(3, '当前仅支持第一方CLI离线插件包')
    const trusted = trust.find(key => key.key_id === envelope.publisher_key_id && key.providers.includes(envelope.provider_id))
    if (!trusted || typeof envelope.signature !== 'string' || !/^base64:[A-Za-z0-9+/]{86}==$/.test(envelope.signature)) invalid('插件发布者不受信任或签名无效')
    const publicKey = publisherPublicKey(trusted.public_key)
    const { signature, ...unsigned } = envelope
    if (publicKey.asymmetricKeyType !== 'ed25519' || !verify(null, Buffer.from(canonicalJson(unsigned)), publicKey, Buffer.from(signature.slice(7), 'base64'))) invalid('插件签名无效')
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(envelope.provider_release_id)
      || !semver.valid(envelope.provider_version) || !semver.valid(envelope.min_runtime_version)
      || (envelope.max_runtime_version !== null && !semver.valid(envelope.max_runtime_version))
      || !Number.isFinite(Date.parse(envelope.created_at))) invalid()
    if (envelope.platform !== platform.platform || envelope.arch !== platform.arch
      || semver.lt(platform.runtime_version, envelope.min_runtime_version)
      || (envelope.max_runtime_version !== null && semver.gt(platform.runtime_version, envelope.max_runtime_version))) invalid('插件平台或Runtime版本不兼容')
    const payload = outer.get('payload.zip')!
    if (envelope.package_size !== payload.length || envelope.package_digest !== `sha256:${sha256(payload)}`) invalid('插件内容摘要无效')
    const files = await readSafeZip(payload)
    const manifestBytes = files.get('runtime-manifest.json')
    if (!manifestBytes || manifestBytes.length > 16 * 1024 ** 2) invalid('插件完整manifest缺失或过大')
    const manifest = json(manifestBytes) as RuntimeManifest
    if (envelope.manifest_digest !== `sha256:${sha256(canonicalJson(manifest))}` || manifest.manifest_version !== 1
      || manifest.provider_id !== envelope.provider_id || manifest.provider_version !== envelope.provider_version
      || typeof manifest.display_name !== 'string' || !manifest.display_name || manifest.display_name.length > 128
      || typeof manifest.description !== 'string' || typeof manifest.schema_digest !== 'string' || !manifest.schema_digest
      || !Array.isArray(manifest.tools) || !manifest.tools.length || manifest.execution_target !== 'local_required'
      || !Array.isArray(manifest.entrypoint) || manifest.entrypoint.length !== 3
      || manifest.entrypoint[1] !== 'mcp' || manifest.entrypoint[2] !== '--stdio'
      || !manifest.entrypoint[0]?.endsWith('.js') || !files.has(safePackagePath(manifest.entrypoint[0]))) invalid('插件manifest契约无效')
    const distro = manifest.distribution
    if (!distro || distro.platform !== platform.platform || distro.arch !== platform.arch || distro.node?.version !== platform.node_version
      || distro.node.modules_abi !== platform.node_abi || !Array.isArray(distro.required_files)) invalid('插件Node或发行依赖不兼容')
    const required = new Set<string>()
    for (const file of distro.required_files) {
      if (!file || typeof file.path !== 'string' || file.path === 'runtime-manifest.json' || required.has(file.path)) invalid()
      safePackagePath(file.path); required.add(file.path)
      const contents = files.get(file.path)
      if (!contents || file.size !== contents.length || !/^[0-9a-f]{64}$/.test(file.sha256) || sha256(contents) !== file.sha256) invalid('插件发行依赖缺失或损坏')
    }
    if (required.size !== files.size - 1) invalid('插件包含未登记文件')
    return { envelope, manifest, files, outer_digest: sha256(bytes) }
  } catch (error) { if (error instanceof ManagementError) throw error; invalid() }
}

/** Only call for a newly created private staging directory after signature verification. */
export function extractVerifiedPackage(pkg: VerifiedPackage, destination: string): void {
  for (const [relative, bytes] of pkg.files) {
    const file = join(destination, safePackagePath(relative)); mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, bytes, { flag: 'wx', mode: 0o600 })
  }
}
