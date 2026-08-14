import { readFile, readdir } from 'node:fs/promises'
import { builtinModules } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const sourceExtensions = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.vue', '.cts', '.mts', '.cjs', '.mjs',
  '.css', '.scss', '.sass', '.less', '.styl',
])
const nodeModules = new Set(builtinModules.map((name) => name.replace(/^node:/, '').split('/', 1)[0]))

function extractScriptDependencies(source, fileName) {
  const specifiers = []
  let hasNonLiteralRuntimeImport = false
  const sourceFile = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, false)
  const addLiteral = (node) => {
    if (node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))) {
      specifiers.push(node.text)
      return true
    }
    return false
  }
  const visit = (node) => {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      addLiteral(node.moduleSpecifier)
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      addLiteral(node.moduleReference.expression)
    } else if (ts.isCallExpression(node)) {
      const isDynamicImport = node.expression.kind === ts.SyntaxKind.ImportKeyword
      const isRequire = ts.isIdentifier(node.expression) && node.expression.text === 'require'
      if ((isDynamicImport || isRequire) && !addLiteral(node.arguments[0])) {
        hasNonLiteralRuntimeImport = true
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return { specifiers, hasNonLiteralRuntimeImport }
}

function extractStyleSpecifiers(source) {
  const specifiers = []
  let index = 0
  let quote
  let inComment = false
  while (index < source.length) {
    if (inComment) {
      if (source.startsWith('*/', index)) { inComment = false; index += 2 } else index += 1
      continue
    }
    if (quote) {
      if (source[index] === '\\') index += 2
      else if (source[index] === quote) { quote = undefined; index += 1 } else index += 1
      continue
    }
    if (source.startsWith('/*', index)) { inComment = true; index += 2; continue }
    if (source[index] === '"' || source[index] === "'") { quote = source[index]; index += 1; continue }
    const directive = source.slice(index).match(/^@(import|use|forward)\b/i)
    if (!directive) { index += 1; continue }
    index += directive[0].length
    while (/\s/.test(source[index] ?? '')) index += 1
    if (source.slice(index, index + 4).toLowerCase() === 'url(') {
      index += 4
      while (/\s/.test(source[index] ?? '')) index += 1
    }
    const startQuote = source[index]
    if (startQuote === '"' || startQuote === "'") {
      const start = ++index
      while (index < source.length && source[index] !== startQuote) index += source[index] === '\\' ? 2 : 1
      specifiers.push(source.slice(start, index))
      if (source[index] === startQuote) index += 1
    } else {
      const start = index
      while (index < source.length && !/[\s;)]/.test(source[index])) index += 1
      if (index > start) specifiers.push(source.slice(start, index))
    }
  }
  return specifiers
}

export function extractDependencies(filePath, source) {
  const extension = path.extname(filePath).toLowerCase()
  if (extension === '.css' || extension === '.scss' || extension === '.sass' || extension === '.less' || extension === '.styl') {
    return { specifiers: extractStyleSpecifiers(source), hasNonLiteralRuntimeImport: false }
  }
  if (extension === '.vue') {
    const vueSource = source.replace(/<!--[\s\S]*?-->/g, '')
    const specifiers = []
    let hasNonLiteralRuntimeImport = false
    for (const match of vueSource.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
      const src = match[1].match(/\bsrc\s*=\s*["']([^"']+)["']/i)?.[1]
      if (src) specifiers.push(src)
      const result = extractScriptDependencies(match[2], `${filePath}.ts`)
      specifiers.push(...result.specifiers)
      hasNonLiteralRuntimeImport ||= result.hasNonLiteralRuntimeImport
    }
    for (const match of vueSource.matchAll(/<style\b([^>]*)>([\s\S]*?)<\/style\s*>/gi)) {
      const src = match[1].match(/\bsrc\s*=\s*["']([^"']+)["']/i)?.[1]
      if (src) specifiers.push(src)
      specifiers.push(...extractStyleSpecifiers(match[2]))
    }
    return { specifiers: [...new Set(specifiers)], hasNonLiteralRuntimeImport }
  }
  const result = extractScriptDependencies(source, filePath)
  return { ...result, specifiers: [...new Set(result.specifiers)] }
}

function normalize(value) {
  return value.replaceAll('\\', '/')
}

function frontendLayer(filePath) {
  const normalized = normalize(filePath).toLowerCase().replace(/^\/+/, '')
  if (normalized.startsWith('web/')) return 'web'
  if (normalized.startsWith('desktop/')) return 'desktop'
  if (normalized.startsWith('shared/')) return 'shared'
  if (normalized.startsWith('frontend/web/')) return 'web'
  if (normalized.startsWith('frontend/desktop/')) return 'desktop'
  if (normalized.startsWith('frontend/shared/')) return 'shared'
  if (normalized.includes('/frontend/web/')) return 'web'
  if (normalized.includes('/frontend/desktop/')) return 'desktop'
  if (normalized.includes('/frontend/shared/')) return 'shared'
  return undefined
}

function importedFrontendLayer(importer, specifier) {
  const normalizedSpecifier = normalize(specifier)
  const lowerSpecifier = normalizedSpecifier.toLowerCase()
  if (lowerSpecifier === '@' || lowerSpecifier.startsWith('@/') || lowerSpecifier === '@web' || lowerSpecifier.startsWith('@web/')) return 'web'
  if (lowerSpecifier === '@desktop' || lowerSpecifier.startsWith('@desktop/')) return 'desktop'
  if (lowerSpecifier === '@shared' || lowerSpecifier.startsWith('@shared/')) return 'shared'
  if (lowerSpecifier.startsWith('/') || /^[a-z]:\//i.test(normalizedSpecifier)) return frontendLayer(lowerSpecifier)
  if (!specifier.startsWith('.')) return undefined
  return frontendLayer(normalize(path.posix.normalize(path.posix.join(path.posix.dirname(normalize(importer)), normalizedSpecifier))))
}

function isNativeRuntimeImport(specifier) {
  const normalizedSpecifier = specifier.toLowerCase()
  if (normalizedSpecifier === 'electron' || normalizedSpecifier.startsWith('electron/')) return true
  if (normalizedSpecifier.startsWith('node:')) return true
  return nodeModules.has(normalizedSpecifier.split('/', 1)[0])
}

function isAllowedDesktopWebImport(importer, specifier, allowlist) {
  return allowlist.some((entry) => entry.importer === importer && entry.specifier === specifier)
}

export function findDependencyViolations(files, allowlist = []) {
  const violations = []
  for (const file of files) {
    const importer = normalize(file.path)
    const layer = frontendLayer(importer)
    const isClientCore = importer.toLowerCase().startsWith('clients/shared/')
    const dependencies = extractDependencies(importer, file.source)
    if ((layer || isClientCore) && dependencies.hasNonLiteralRuntimeImport) {
      violations.push(`${importer}: non-literal dynamic import cannot be dependency-scanned`)
    }
    for (const specifier of dependencies.specifiers) {
      const targetLayer = importedFrontendLayer(importer, specifier)
      const normalizedSpecifier = normalize(specifier)
      const resolvedRelative = specifier.startsWith('.')
        ? normalize(path.posix.normalize(path.posix.join(path.posix.dirname(importer), normalizedSpecifier)))
        : undefined
      if (layer && resolvedRelative && !frontendLayer(resolvedRelative)) {
        violations.push(`${importer}: frontend source import escapes web/desktop/shared roots: "${specifier}"`)
        continue
      }
      if (layer && isNativeRuntimeImport(specifier)) {
        violations.push(`${importer}: frontend source cannot import native runtime module "${specifier}"`)
      } else if (layer === 'shared' && (targetLayer === 'web' || targetLayer === 'desktop')) {
        violations.push(`${importer}: shared cannot import ${targetLayer} module "${specifier}"`)
      } else if (layer === 'web' && targetLayer === 'desktop') {
        violations.push(`${importer}: web cannot import desktop module "${specifier}"`)
      } else if (layer === 'desktop' && targetLayer === 'web' && !isAllowedDesktopWebImport(importer, specifier, allowlist)) {
        violations.push(`${importer}: desktop-to-web import is not allowlisted: "${specifier}"`)
      }

      if (isClientCore && (
        specifier.toLowerCase() === 'electron' || specifier.toLowerCase().startsWith('electron/')
        || targetLayer === 'web' || targetLayer === 'desktop'
        || normalize(specifier).toLowerCase().includes('frontend/')
      )) {
        violations.push(`${importer}: client core cannot import UI/Electron module "${specifier}"`)
      }
      if (importer.toLowerCase().startsWith('clients/shared/local-tool-host-core/') && specifier.toLowerCase().includes('agent-coordinator-core')) {
        violations.push(`${importer}: local-tool-host-core cannot depend on agent-coordinator-core`)
      }
    }
  }
  return violations
}

export function filesForScope(files, scope) {
  if (scope === 'full') return files
  if (scope === 'web') {
    const normalizedFiles = new Map(files.map((file) => [normalize(file.path).toLowerCase(), file]))
    const selected = new Map()
    const queue = files.filter((file) => frontendLayer(file.path) === 'web')
    const resolvableExtensions = ['', '.ts', '.tsx', '.js', '.jsx', '.vue', '.cts', '.mts', '.cjs', '.mjs', '.css', '.scss', '.sass', '.less', '.styl']
    while (queue.length > 0) {
      const file = queue.shift()
      const normalizedPath = normalize(file.path).toLowerCase()
      if (selected.has(normalizedPath)) continue
      selected.set(normalizedPath, file)
      for (const specifier of extractDependencies(file.path, file.source).specifiers) {
        if (importedFrontendLayer(file.path, specifier) !== 'shared') continue
        const cleanSpecifier = normalize(specifier).split(/[?#]/, 1)[0]
        const basePath = cleanSpecifier.toLowerCase().startsWith('@shared/')
          ? `shared/${cleanSpecifier.slice('@shared/'.length)}`
          : normalize(path.posix.normalize(path.posix.join(path.posix.dirname(normalize(file.path)), cleanSpecifier)))
        const candidates = resolvableExtensions.flatMap((extension) => [
          `${basePath}${extension}`,
          `${basePath}/index${extension}`,
        ])
        const dependency = candidates.map((candidate) => normalizedFiles.get(candidate.toLowerCase())).find(Boolean)
        if (dependency && !selected.has(normalize(dependency.path).toLowerCase())) queue.push(dependency)
      }
    }
    return [...selected.values()]
  }
  throw new Error(`Unsupported dependency boundary scope: ${scope}`)
}

async function collectFiles(root, relativeRoot) {
  const directory = path.join(root, relativeRoot)
  const entries = await readdir(directory, { withFileTypes: true }).catch((error) => {
    if (error?.code === 'ENOENT') return []
    throw error
  })
  const files = []
  for (const entry of entries) {
    if (['node_modules', 'dist', 'dist-desktop', '__tests__', '__fixtures__', 'fixtures', 'test', 'tests'].includes(entry.name)) continue
    const relativePath = normalize(path.join(relativeRoot, entry.name))
    if (entry.isDirectory()) files.push(...await collectFiles(root, relativePath))
    else if (sourceExtensions.has(path.extname(entry.name))) {
      files.push({ path: relativePath, source: await readFile(path.join(root, relativePath), 'utf8') })
    }
  }
  return files
}

function readScope(argumentsList) {
  const value = argumentsList.find((argument) => argument.startsWith('--scope='))?.slice('--scope='.length) ?? 'full'
  if (value !== 'web' && value !== 'full') throw new Error(`Unsupported dependency boundary scope: ${value}`)
  return value
}

async function main(argumentsList = process.argv.slice(2)) {
  const scope = readScope(argumentsList)
  const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
  const frontendRoot = path.resolve(scriptDirectory, '..')
  const repositoryRoot = path.resolve(frontendRoot, '..')
  const allowlistDocument = JSON.parse(await readFile(path.join(frontendRoot, 'config/desktop-web-import-allowlist.json'), 'utf8'))
  const allowlist = allowlistDocument.entries ?? []
  for (const entry of allowlist) {
    if (!entry.importer || !entry.specifier || !entry.owner || !entry.removeByPhase) {
      throw new Error('Desktop Web import allowlist entries require importer, specifier, owner and removeByPhase')
    }
  }
  const webFiles = await collectFiles(frontendRoot, 'web')
  const sharedFiles = await collectFiles(frontendRoot, 'shared')
  const fullOnlyFiles = scope === 'full' ? [
    ...await collectFiles(frontendRoot, 'desktop'),
    ...[
      ...await collectFiles(repositoryRoot, 'clients/shared/agent-coordinator-core'),
      ...await collectFiles(repositoryRoot, 'clients/shared/local-tool-host-core'),
    ].map((file) => ({ ...file, path: normalize(file.path) })),
  ] : []
  const scopedFiles = filesForScope([...webFiles, ...sharedFiles, ...fullOnlyFiles], scope)
  const violations = findDependencyViolations(scopedFiles, allowlist)
  if (violations.length > 0) throw new Error(`Dependency boundary violations:\n${violations.join('\n')}`)
  process.stdout.write(`Dependency boundary verification passed for ${scope} scope (${scopedFiles.length} source files).\n`)
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : error)
    process.exitCode = 1
  })
}
