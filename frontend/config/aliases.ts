import path from 'node:path'

const frontendRoot = path.resolve(__dirname, '..')

/** Vite、Vitest 共用的唯一 alias 定义，TypeScript paths 与这里保持同名。 */
export const frontendAliases = {
  '@': path.resolve(frontendRoot, 'web'),
  '@web': path.resolve(frontendRoot, 'web'),
  '@desktop': path.resolve(frontendRoot, 'desktop'),
  '@shared': path.resolve(frontendRoot, 'shared'),
}
