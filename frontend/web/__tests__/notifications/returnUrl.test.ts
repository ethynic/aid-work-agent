import { describe, expect, it } from 'vitest'
import { notificationReturnUrl } from '@/notifications/returnUrl'
describe('notification login return URL', () => {
  it('keeps only a safe reference and tenant', () => {
    expect(notificationReturnUrl('/notification?ref=abc-123&tenant=tenant_1&url=https://evil.example')).toBe('/notification?ref=abc-123&tenant=tenant_1')
  })
  it.each(['https://evil.example/notification?ref=x', '//evil.example/notification?ref=x', '/notification?ref=<script>', '/notification?ref=x#bad', '/portal?ref=x', null])('rejects unsafe return %s', value => {
    expect(notificationReturnUrl(value)).toBeNull()
  })
  it('returns to a single public task result after tenant login', () => {
    expect(notificationReturnUrl('/share/task-result/safe_key-1')).toBe('/share/task-result/safe_key-1')
  })
  it.each(['/share/task-result/a?next=//evil.example', '/share/task-result/a#bad',
    '/share/task-result/a/../b', '//evil.example/share/task-result/a', '/share/task-result/%2e%2e'])('rejects unsafe task result return %s', value => {
    expect(notificationReturnUrl(value)).toBeNull()
  })
})
