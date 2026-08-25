import { readFile, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const normalize = (value) => value.replaceAll('\\', '/').toLowerCase()

export function findForbiddenWebModules(modules, forbiddenModules) {
  return forbiddenModules.filter((forbidden) => {
    const normalizedForbidden = normalize(forbidden)
    return modules.some((moduleId) => {
      const normalizedModule = normalize(moduleId)
      return normalizedModule.startsWith(normalizedForbidden) || normalizedModule.includes(`/${normalizedForbidden}`)
    })
  })
}

export async function verifyWebBuild(dist, baselinePath) {
  const manifestPath = path.join(dist, 'web-module-manifest.json')
  const baseline = JSON.parse(await readFile(baselinePath, 'utf8'))
  const modules = JSON.parse(await readFile(manifestPath, 'utf8'))
  const html = await readFile(path.join(dist, 'index.html'), 'utf8')
  const forbidden = findForbiddenWebModules(modules, baseline.bundleForbiddenModules)
  if (forbidden.length > 0) throw new Error(`Web build contains Desktop-only modules:\n${forbidden.join('\n')}`)
  if (!html.includes('/assets/')) throw new Error('Web build index does not reference production assets')
  await rm(manifestPath)
  return modules.length
}

async function main(distArgument) {
  const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
  const frontendRoot = path.resolve(scriptDirectory, '..')
  const dist = distArgument ? path.resolve(distArgument) : path.join(frontendRoot, 'dist')
  const count = await verifyWebBuild(dist, path.join(frontendRoot, 'baselines/web-structure.json'))
  process.stdout.write(`Web artifact verification passed (${count} bundled modules; verification manifest removed).\n`)
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv[2]).catch((error) => {
    console.error(error instanceof Error ? error.message : error)
    process.exitCode = 1
  })
}
