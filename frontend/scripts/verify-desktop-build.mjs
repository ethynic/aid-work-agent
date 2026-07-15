import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const forbiddenModules = [
  'src/router/portalRoutes.ts',
  'src/components/saas/PortalLayout.vue',
  'src/components/DigitalEmployeeManager.vue',
  'src/api/adminSubagent.ts',
  'src/components/saas/TenantDashboard.vue',
  'src/components/saas/TenantMgmt.vue',
  'src/components/AgentDefinitionManager.vue',
  'src/components/saas/PlatformTokenUsage.vue',
  'src/components/saas/ErrorLogs.vue',
  'src/components/saas/SystemReplyStyleManager.vue',
  'src/components/saas/TraceBrowser.vue',
  'src/components/saas/SessionTraces.vue',
  'src/components/saas/TraceDetail.vue',
  'src/components/saas/ContextCompressionManager.vue',
  'src/components/saas/RpaBindingPanel.vue',
  'src/components/saas/RedisCacheManager.vue',
]

const normalizeModulePath = (value) => {
  const withoutQuery = value.split(/[?#]/, 1)[0].replaceAll('\\', '/')
  return withoutQuery.replace(/^\0+/, '').toLowerCase()
}

const matchesForbiddenModule = (modulePath, forbidden) => {
  const normalizedForbidden = normalizeModulePath(forbidden)
  return modulePath === normalizedForbidden || modulePath.endsWith(`/${normalizedForbidden}`)
}

export function findForbiddenDesktopModules(manifest, bundledModules = []) {
  const modulePaths = new Set()
  for (const [key, entry] of Object.entries(manifest)) {
    modulePaths.add(normalizeModulePath(key))
    if (entry && typeof entry === 'object' && typeof entry.src === 'string') {
      modulePaths.add(normalizeModulePath(entry.src))
    }
  }
  for (const modulePath of bundledModules) {
    modulePaths.add(normalizeModulePath(modulePath))
  }
  return forbiddenModules.filter((forbidden) =>
    [...modulePaths].some((modulePath) => matchesForbiddenModule(modulePath, forbidden))
  )
}

export function findDeepLinkRelativeAssets(html, deepLink = 'aidagent://app/t/example/chat') {
  const assetReferences = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((match) => match[1])
  return assetReferences.filter((reference) => new URL(reference, deepLink).pathname.startsWith('/t/'))
}

export function findForbiddenDesktopBundleText(source) {
  const findings = []
  if (/portal_token/i.test(source)) findings.push('portal_token')
  if (/localStorage\.(?:getItem|setItem|removeItem)\(\s*["'](?:demo_token|user_info|saas_(?:token|admin|tenant)(?:_[^"']*)?)["']/i.test(source)) {
    findings.push('sensitive-localStorage')
  }
  return findings
}

async function readJavaScriptBundle(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const contents = []
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) contents.push(await readJavaScriptBundle(entryPath))
    else if (/\.(?:m?js)$/i.test(entry.name)) contents.push(await readFile(entryPath, 'utf8'))
  }
  return contents.join('\n')
}

async function main(distDirectoryArgument) {
  const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
  const distDirectory = distDirectoryArgument
    ? path.resolve(distDirectoryArgument)
    : path.resolve(scriptDirectory, '../dist-desktop')
  const manifestPath = path.join(distDirectory, '.vite/manifest.json')
  const moduleManifestPath = path.join(distDirectory, 'desktop-module-manifest.json')
  const htmlPath = path.join(distDirectory, 'desktop.html')
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const bundledModules = JSON.parse(await readFile(moduleManifestPath, 'utf8'))
  const html = await readFile(htmlPath, 'utf8')
  const bundleSource = await readJavaScriptBundle(distDirectory)
  const forbiddenFound = findForbiddenDesktopModules(manifest, bundledModules)
  if (forbiddenFound.length > 0) {
    throw new Error(`Desktop build contains Portal-only modules:\n${forbiddenFound.join('\n')}`)
  }
  const deepLinkRelativeAssets = findDeepLinkRelativeAssets(html)
  if (deepLinkRelativeAssets.length > 0) {
    throw new Error(`Desktop build contains deep-link-relative assets:\n${deepLinkRelativeAssets.join('\n')}`)
  }
  const forbiddenBundleText = findForbiddenDesktopBundleText(bundleSource)
  if (forbiddenBundleText.length > 0) {
    throw new Error(`Desktop build contains forbidden credential persistence text:\n${forbiddenBundleText.join('\n')}`)
  }
  process.stdout.write(
    `Desktop artifact verification passed (${Object.keys(manifest).length} manifest entries, ${bundledModules.length} bundled modules).\n`
  )
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv[2]).catch((error) => {
    console.error(error instanceof Error ? error.message : error)
    process.exitCode = 1
  })
}
