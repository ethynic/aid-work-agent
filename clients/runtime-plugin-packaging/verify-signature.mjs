import { readFileSync } from 'node:fs'
import { verify } from 'node:crypto'
import canonicalize from 'canonicalize'
import { sha256 } from './package.mjs'
import { normalizeTrust } from '../agent-desktop/scripts/prepare-runtime.mjs'

const [envelopePath, payloadPath, manifestPath, trustPath] = process.argv.slice(2)
const envelope = JSON.parse(readFileSync(envelopePath, 'utf8'))
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const trust = JSON.parse(readFileSync(trustPath, 'utf8'))
if (!['acceptance', 'production'].includes(trust.profile)) throw new Error('Explicit acceptance or production publisher trust required')
normalizeTrust(trust, trust.profile)
const root = trust.roots.find(root => root.key_id === envelope.publisher_key_id && root.providers.includes(envelope.provider_id))
if (!root) throw new Error('Publisher is not trusted for this Provider')
const { signature, ...unsigned } = envelope
if (!verify(null, Buffer.from(canonicalize(unsigned)), root.public_key, Buffer.from(signature.slice(7), 'base64'))) throw new Error('Invalid signature')
const payload = readFileSync(payloadPath)
if (envelope.package_size !== payload.length || envelope.package_digest !== 'sha256:' + sha256(payload)) throw new Error('Invalid payload digest')
if (envelope.manifest_digest !== 'sha256:' + sha256(Buffer.from(canonicalize(manifest)))) throw new Error('Invalid manifest digest')
console.log('SIGNATURE_OK')
