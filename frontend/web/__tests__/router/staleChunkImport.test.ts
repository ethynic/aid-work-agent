import { describe, expect, it } from 'vitest'

import { isStaleChunkImportError } from '@/app/bootstrap'

describe('isStaleChunkImportError', () => {
  it('识别 Chrome 的动态导入失败报错', () => {
    const error = new TypeError('Failed to fetch dynamically imported module: https://agent2.aidingyi.cn/assets/BehaviorLogs-CLX_ClAY.js')
    expect(isStaleChunkImportError(error)).toBe(true)
  })

  it('识别 Firefox 的动态导入失败报错', () => {
    const error = new TypeError('error loading dynamically imported module: https://agent2.aidingyi.cn/assets/index-DdzZXmZ6.js')
    expect(isStaleChunkImportError(error)).toBe(true)
  })

  it('识别 Safari 的动态导入失败报错', () => {
    const error = new TypeError('Importing a module script failed.')
    expect(isStaleChunkImportError(error)).toBe(true)
  })

  it('不误伤其他路由错误', () => {
    expect(isStaleChunkImportError(new Error('Navigation cancelled from "/" to "/t/1/agent"'))).toBe(false)
    expect(isStaleChunkImportError(new TypeError('Cannot read properties of undefined'))).toBe(false)
    expect(isStaleChunkImportError(undefined)).toBe(false)
  })
})
