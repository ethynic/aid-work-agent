import path from 'node:path'

export function moduleManifest(fileName: string) {
  return {
    name: `${fileName}-plugin`,
    generateBundle(_: unknown, bundle: Record<string, { type: string; modules?: Record<string, unknown> }>) {
      const modules = new Set<string>()
      for (const output of Object.values(bundle)) {
        if (output.type !== 'chunk' || !output.modules) continue
        for (const moduleId of Object.keys(output.modules)) {
          const cleanId = moduleId.split('?')[0]
          if (path.isAbsolute(cleanId)) {
            modules.add(path.relative(process.cwd(), cleanId).replaceAll('\\', '/'))
          }
        }
      }
      this.emitFile({
        type: 'asset',
        fileName,
        source: `${JSON.stringify([...modules].sort(), null, 2)}\n`,
      })
    },
  }
}
