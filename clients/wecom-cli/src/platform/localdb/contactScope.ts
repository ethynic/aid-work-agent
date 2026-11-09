/** Caller intent only: DB relationship evidence must still confirm it. */
export function hasExternalWechatHint(name: string, type?: string, subtitle = ''): boolean {
  return type !== 'group' && (name.trim().endsWith('@微信') || (type === 'contact' && subtitle.trim() === '微信联系人'))
}
