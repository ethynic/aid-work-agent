import { access, copyFile, cp, rm } from 'node:fs/promises'
import path from 'node:path'

const frontendDist = path.resolve('../../frontend/dist-desktop')
const rendererDist = path.resolve('dist/renderer')

await access(path.join(frontendDist, 'desktop.html'))
await access('dist/electron/preload.cjs')
await rm(rendererDist, { recursive: true, force: true })
await cp(frontendDist, rendererDist, { recursive: true })
await copyFile(path.join(rendererDist, 'desktop.html'), path.join(rendererDist, 'index.html'))
