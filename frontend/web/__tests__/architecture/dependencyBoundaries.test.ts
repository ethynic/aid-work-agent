import { describe, expect, it } from 'vitest'

// @ts-expect-error 可执行 ESM 门禁脚本没有 TypeScript 声明。
import { filesForScope, findDependencyViolations } from '../../../scripts/check-dependency-boundaries.mjs'

describe('frontend dependency boundaries', () => {
  it('accepts the intended web/desktop to shared dependency direction', () => {
    expect(findDependencyViolations([
      { path: 'web/consumer.ts', source: ['import type', '{ Contract }', "from '@shared/contracts'"].join(' ') },
      { path: 'desktop/consumer.ts', source: ['import', '{ normalize }', "from '../shared/normalize'"].join(' ') },
      { path: 'shared/contracts.ts', source: 'export interface Contract { id: string }' },
    ])).toEqual([])
  })

  it.each([
    ['shared/value.ts', ['import App', "from '@web/App.vue'"].join(' '), 'shared cannot import web'],
    ['shared/value.ts', ['export { value }', "from '@desktop/value'"].join(' '), 'shared cannot import desktop'],
    ['shared/value.ts', ['import { ipcRenderer }', "from 'electron'"].join(' '), 'native runtime'],
    ['web/value.ts', "import os from 'node:os'", 'native runtime'],
    ['desktop/value.ts', "import vm from 'vm'", 'native runtime'],
    ['shared/value.ts', "import diagnostics from 'node:diagnostics_channel'", 'native runtime'],
    ['web/value.ts', ['const module = im', "port('@desktop/value')"].join(''), 'web cannot import desktop'],
    ['desktop/value.ts', ['import { routes }', "from '@/router/agentRoutes'"].join(' '), 'not allowlisted'],
    ['web/value.ts', ['im', "port('/desktop/value')"].join(''), 'web cannot import desktop'],
    ['web/value.ts', ['im', "port('../Desktop/value')"].join(''), 'web cannot import desktop'],
    ['shared/value.scss', "@use '../web/style'", 'shared cannot import web'],
    ['shared/value.css', "@import url('../web/style.css')", 'shared cannot import web'],
    ['shared/value.css', '@import url(../web/print.css) print;', 'shared cannot import web'],
    ['web/value.ts', "import 'C:\\\\repo\\\\frontend\\\\desktop\\\\value'", 'web cannot import desktop'],
    ['shared/nested/value.ts', "import value from '../../../clients/value'", 'escapes web/desktop/shared roots'],
    ['shared/value.vue', "<script setup lang=\"ts\">import value from '@web/value'</script>", 'shared cannot import web'],
    ['shared/value.vue', "<script src=\"@web/value.ts\"></script>", 'shared cannot import web'],
    ['shared/value.vue', "<style src=\"../web/value.css\"></style>", 'shared cannot import web'],
    ['clients/shared/agent-coordinator-core/src/value.ts', ['import', "'@desktop/value'"].join(' '), 'client core cannot import UI'],
    ['clients/shared/local-tool-host-core/src/value.ts', ['import', "'@web/value'"].join(' '), 'client core cannot import UI'],
  ])('rejects %s -> %s', (filePath, source, expected) => {
    expect(findDependencyViolations([{ path: filePath, source }])).toEqual([
      expect.stringContaining(expected),
    ])
  })

  it.each([
    ['web/value.ts', ['im', 'port(`@desktop/${name}`)'].join('')],
    ['desktop/value.ts', ['im', 'port(targetModule)'].join('')],
    ['shared/value.ts', ['im', 'port(resolveSharedModule())'].join('')],
    ['clients/shared/agent-coordinator-core/src/value.ts', ['im', 'port(moduleName)'].join('')],
    ['clients/shared/local-tool-host-core/src/value.mjs', 'require(moduleName)'],
  ])('rejects non-literal dynamic imports that cannot be dependency-scanned in %s', (filePath, source) => {
    expect(findDependencyViolations([{ path: filePath, source }])).toEqual([
      expect.stringContaining('non-literal dynamic import'),
    ])
  })

  it.each([
    ['web/value.ts', "// import value from '@desktop/value'"],
    ['web/value.ts', "const example = \"import value from '@desktop/value'\""],
    ['shared/value.vue', "<!-- import value from '@web/value' --><template><p>ready</p></template>"],
    ['shared/value.css', "/* @import '../web/value.css'; */ .label::before { content: \"@import '../web/value.css'\"; }"],
    ['shared/value.vue', "<!-- <script>import value from '@web/value'</script> --><template><p>ready</p></template>"],
  ])('ignores non-executable dependency text in %s', (filePath, source) => {
    expect(findDependencyViolations([{ path: filePath, source }])).toEqual([])
  })

  it('scans every stylesheet dependency after quoted directives', () => {
    const source = "@import 'theme.css'; @use '../web/tokens';"
    expect(findDependencyViolations([{ path: 'shared/value.scss', source }])).toEqual([
      expect.stringContaining('shared cannot import web'),
    ])
  })

  it('requires an exact Desktop-to-Web allowlist match', () => {
    const files = [{ path: 'desktop/legacy.ts', source: ['import Widget', "from '@web/components/Leaf.vue'"].join(' ') }]
    const allowlist = [{
      importer: 'desktop/legacy.ts',
      specifier: '@web/components/Leaf.vue',
      owner: 'desktop-team',
      removeByPhase: 'E1',
    }]
    expect(findDependencyViolations(files, allowlist)).toEqual([])
  })

  it('isolates Web production scope from Desktop and client-core violations', () => {
    const files = [
      { path: 'web/healthy.ts', source: "export const healthy = true" },
      { path: 'desktop/broken.ts', source: "import app from '@web/App.vue'" },
      { path: 'clients/shared/agent-coordinator-core/src/broken.ts', source: "import { ipcRenderer } from 'electron'" },
    ]
    expect(findDependencyViolations(filesForScope(files, 'web'))).toEqual([])
    expect(findDependencyViolations(filesForScope(files, 'full'))).toEqual([
      expect.stringContaining('desktop-to-web import is not allowlisted'),
      expect.stringContaining('client core cannot import UI/Electron'),
    ])
  })

  it('checks the reachable Shared dependency closure without scanning unrelated Shared files', () => {
    const files = [
      { path: 'web/consumer.ts', source: "import { value } from '@shared/reachable'" },
      { path: 'shared/reachable.ts', source: "export { value } from './nested/value'" },
      { path: 'shared/nested/value.ts', source: "import App from '@web/App.vue'; export const value = App" },
      { path: 'shared/unrelated.ts', source: "import { ipcRenderer } from 'electron'" },
    ]
    const scopedFiles = filesForScope(files, 'web')
    expect(scopedFiles.map((file: { path: string }) => file.path)).toEqual([
      'web/consumer.ts',
      'shared/reachable.ts',
      'shared/nested/value.ts',
    ])
    expect(findDependencyViolations(scopedFiles)).toEqual([
      expect.stringContaining('shared cannot import web'),
    ])
  })

  it('rejects Web violations in both Web and full scopes', () => {
    const files = [{ path: 'web/broken.ts', source: "import { ipcRenderer } from 'electron'" }]
    expect(findDependencyViolations(filesForScope(files, 'web'))).toEqual([expect.stringContaining('native runtime')])
    expect(findDependencyViolations(filesForScope(files, 'full'))).toEqual([expect.stringContaining('native runtime')])
  })

  it('rejects unknown dependency scopes instead of silently widening or narrowing them', () => {
    expect(() => filesForScope([], 'desktop')).toThrow('Unsupported dependency boundary scope')
  })
})
