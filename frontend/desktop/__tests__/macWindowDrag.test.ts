import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const desktopStyles = readFileSync('desktop/styles/desktop.css', 'utf8')

describe('macOS window drag regions', () => {
  it('keeps unauthenticated surfaces draggable without swallowing form or blocker interactions', () => {
    expect(desktopStyles).toContain(
      'html[data-platform="darwin"] .login-context { -webkit-app-region: drag; user-select: none; }',
    )
    expect(desktopStyles).toContain(
      'html[data-platform="darwin"] .blocker-layout { -webkit-app-region: drag; }',
    )
    expect(desktopStyles).toContain(
      'html[data-platform="darwin"] .login-panel, html[data-platform="darwin"] .blocker-card { -webkit-app-region: no-drag; }',
    )
    expect(desktopStyles).toContain('button, a, input { -webkit-app-region: no-drag; }')
  })
})
