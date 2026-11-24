/**
 * 连接邮箱品牌表单测
 *
 * 验证点：
 * - visibleMailboxGroups：分组标题与顺序、预留品牌（Outlook/iCloud）不出现
 * - resolveMailboxAddress：suffix 品牌补域名、含 @ 原样、非 suffix 原样
 * - buildMailboxConfigs：各品牌服务器组数量与参数（含网易免费版 SMTP 994）、custom 表单值
 * - detectMailboxBrand：按 smtp_server 识别品牌（含信创/华东/免费版）、大小写、兜底 custom
 * - pickMailboxFailure：auth → 品牌提示；connect/timeout/network → 网络提示；其余 → 通用提示
 */
import { describe, it, expect } from 'vitest'
import {
  visibleMailboxGroups,
  getMailboxBrand,
  resolveMailboxAddress,
  buildMailboxConfigs,
  detectMailboxBrand,
  pickMailboxFailure,
  type MailboxConnectForm,
} from '@/components/connections/mailboxBrands'
import type { EmailSettingsConfig } from '@/api/connectMailbox'

function customForm(overrides: Partial<MailboxConnectForm> = {}): MailboxConnectForm {
  return {
    account: 'li.wei@contoso.com',
    secret: 'app-password',
    smtpServer: 'smtp.contoso.com',
    imapServer: 'imap.contoso.com',
    smtpPort: '465',
    smtpEncryption: 'ssl',
    imapPort: '993',
    imapEncryption: 'ssl',
    smtpUser: '',
    ...overrides,
  }
}

describe('visibleMailboxGroups', () => {
  it('两组标题与顺序正确，预留品牌不出现', () => {
    const groups = visibleMailboxGroups()
    expect(groups.map(g => g.key)).toEqual(['personal', 'enterprise'])
    expect(groups.map(g => g.title)).toEqual(['个人邮箱', '企业邮箱'])

    const [personal, enterprise] = groups
    expect(personal.brands.map(b => b.key)).toEqual(['qq', '163'])
    expect(personal.brands.map(b => b.name)).toEqual(['QQ邮箱', '163邮箱'])
    expect(enterprise.brands.map(b => b.key)).toEqual(['exmail', 'qiye', 'custom'])
    expect(enterprise.brands.map(b => b.name)).toEqual(['腾讯企业邮箱', '网易企业邮箱', '自定义邮箱'])

    const allNames = groups.map(g => g.brands.map(b => b.name)).flat()
    expect(allNames).not.toContain('Outlook')
    expect(allNames).not.toContain('iCloud 邮箱')
  })

  it('企业邮箱组带说明文案', () => {
    const [, enterprise] = visibleMailboxGroups()
    expect(enterprise.desc).toContain('公司邮箱一般使用企业自己的域名')
  })
})

describe('resolveMailboxAddress', () => {
  it('suffix 品牌不含 @ 时补上固定域名，并去掉首尾空白', () => {
    const qq = getMailboxBrand('qq')!
    expect(resolveMailboxAddress(qq, '987654321')).toBe('987654321@qq.com')
    expect(resolveMailboxAddress(qq, '  zhangsan ')).toBe('zhangsan@qq.com')

    const brand163 = getMailboxBrand('163')!
    expect(resolveMailboxAddress(brand163, 'zhangsan')).toBe('zhangsan@163.com')
  })

  it('suffix 品牌含 @ 时原样返回', () => {
    const qq = getMailboxBrand('qq')!
    expect(resolveMailboxAddress(qq, 'user@qq.com')).toBe('user@qq.com')
  })

  it('非 suffix 类型原样返回（去掉首尾空白）', () => {
    const exmail = getMailboxBrand('exmail')!
    expect(resolveMailboxAddress(exmail, ' zhang.wei@minghua.com ')).toBe('zhang.wei@minghua.com')

    const custom = getMailboxBrand('custom')!
    expect(resolveMailboxAddress(custom, 'li.wei@contoso.com')).toBe('li.wei@contoso.com')
  })
})

describe('buildMailboxConfigs', () => {
  it('QQ邮箱生成 1 项配置：smtp.qq.com:465 / imap.qq.com:993，SSL', () => {
    const configs = buildMailboxConfigs(getMailboxBrand('qq')!, customForm({ account: ' 987654321 ', secret: ' authcode16 ' }))
    expect(configs).toHaveLength(1)
    expect(configs[0]).toEqual({
      email_address: '987654321@qq.com',
      smtp_server: 'smtp.qq.com',
      smtp_port: 465,
      smtp_user: '987654321@qq.com',
      smtp_password: 'authcode16',
      smtp_encryption: 'ssl',
      imap_server: 'imap.qq.com',
      imap_port: 993,
      imap_encryption: 'ssl',
    })
  })

  it('163邮箱生成 1 项配置：smtp.163.com:465 / imap.163.com:993，SSL', () => {
    const configs = buildMailboxConfigs(getMailboxBrand('163')!, customForm({ account: 'zhangsan', secret: 'authcode' }))
    expect(configs).toHaveLength(1)
    expect(configs[0].smtp_server).toBe('smtp.163.com')
    expect(configs[0].smtp_port).toBe(465)
    expect(configs[0].imap_server).toBe('imap.163.com')
    expect(configs[0].imap_port).toBe(993)
    expect(configs[0].smtp_encryption).toBe('ssl')
    expect(configs[0].imap_encryption).toBe('ssl')
    expect(configs[0].email_address).toBe('zhangsan@163.com')
  })

  it('腾讯企业邮箱生成 2 项配置：通用与信创两套服务器', () => {
    const configs = buildMailboxConfigs(getMailboxBrand('exmail')!, customForm({ account: 'zhang.wei@minghua.com', secret: 'pwd' }))
    expect(configs).toHaveLength(2)
    expect(configs[0].smtp_server).toBe('smtp.exmail.qq.com')
    expect(configs[0].imap_server).toBe('imap.exmail.qq.com')
    expect(configs[1].smtp_server).toBe('xcsmtp.exmail.qq.com')
    expect(configs[1].imap_server).toBe('xcimap.exmail.qq.com')
    for (const c of configs) {
      expect(c.smtp_port).toBe(465)
      expect(c.imap_port).toBe(993)
      expect(c.smtp_encryption).toBe('ssl')
      expect(c.imap_encryption).toBe('ssl')
      expect(c.email_address).toBe('zhang.wei@minghua.com')
      expect(c.smtp_user).toBe('zhang.wei@minghua.com')
    }
  })

  it('网易企业邮箱生成 3 项配置：付费华北、付费华东、免费版（SMTP 端口 994）', () => {
    const configs = buildMailboxConfigs(getMailboxBrand('qiye')!, customForm({ account: 'zhang.wei@minghua.com', secret: 'authcode' }))
    expect(configs).toHaveLength(3)
    expect(configs[0].smtp_server).toBe('smtp.qiye.163.com')
    expect(configs[1].smtp_server).toBe('smtphz.qiye.163.com')
    expect(configs[2].smtp_server).toBe('smtp.ym.163.com')
    expect(configs.map(c => c.smtp_port)).toEqual([465, 465, 994])
    expect(configs.map(c => c.imap_port)).toEqual([993, 993, 993])
    expect(configs[2].imap_server).toBe('imap.ym.163.com')
    for (const c of configs) {
      expect(c.smtp_encryption).toBe('ssl')
      expect(c.imap_encryption).toBe('ssl')
      expect(c.email_address).toBe('zhang.wei@minghua.com')
      expect(c.smtp_user).toBe('zhang.wei@minghua.com')
    }
  })

  it('自定义邮箱用表单值：端口默认 465 / 993，smtpUser 留空取邮箱地址', () => {
    const configs = buildMailboxConfigs(getMailboxBrand('custom')!, customForm())
    expect(configs).toHaveLength(1)
    expect(configs[0]).toEqual({
      email_address: 'li.wei@contoso.com',
      smtp_server: 'smtp.contoso.com',
      smtp_port: 465,
      smtp_user: 'li.wei@contoso.com',
      smtp_password: 'app-password',
      smtp_encryption: 'ssl',
      imap_server: 'imap.contoso.com',
      imap_port: 993,
      imap_encryption: 'ssl',
    })
  })

  it('自定义邮箱 smtpUser 已填时用表单值', () => {
    const configs = buildMailboxConfigs(getMailboxBrand('custom')!, customForm({ smtpUser: ' li.wei ' }))
    expect(configs[0].smtp_user).toBe('li.wei')
  })
})

describe('detectMailboxBrand', () => {
  function configOf(smtpServer: string): EmailSettingsConfig {
    return {
      email_address: 'a@b.com', smtp_server: smtpServer, smtp_port: 465, smtp_user: 'a@b.com',
      smtp_password: '****', smtp_encryption: 'ssl', imap_server: 'imap.b.com', imap_port: 993, imap_encryption: 'ssl',
    }
  }

  it('四套品牌服务器各举一例', () => {
    expect(detectMailboxBrand(configOf('smtp.qq.com'))).toBe('qq')
    expect(detectMailboxBrand(configOf('smtp.163.com'))).toBe('163')
    expect(detectMailboxBrand(configOf('xcsmtp.exmail.qq.com'))).toBe('exmail')
    expect(detectMailboxBrand(configOf('smtphz.qiye.163.com'))).toBe('qiye')
    expect(detectMailboxBrand(configOf('smtp.ym.163.com'))).toBe('qiye')
  })

  it('大小写不同也能识别', () => {
    expect(detectMailboxBrand(configOf('SMTP.QQ.COM'))).toBe('qq')
  })

  it('其他服务器返回 custom', () => {
    expect(detectMailboxBrand(configOf('smtp.example.com'))).toBe('custom')
  })
})

describe('pickMailboxFailure', () => {
  it('有 auth 时返回该品牌的提示，企业邮箱带 helpLinkText', () => {
    const exmail = pickMailboxFailure('exmail', ['auth'])
    expect(exmail.title).toBe('已尝试所有服务器，请检查填写内容')
    expect(exmail.helpLinkText).toBe('查看填写和开通步骤')

    const qq = pickMailboxFailure('qq', [null, 'auth'])
    expect(qq.title).toBe('授权码不正确，或未开启 IMAP/SMTP 服务')
    expect(qq.text).toContain('QQ 邮箱')
    // 设计 3.4：失败提示里个人邮箱附「查看获取步骤」，企业邮箱附「查看填写和开通步骤」
    expect(qq.helpLinkText).toBe('查看获取步骤')
    expect(pickMailboxFailure('163', ['auth']).helpLinkText).toBe('查看获取步骤')
  })

  it('只有 connect / timeout / network 时返回网络提示', () => {
    expect(pickMailboxFailure('qiye', ['connect', 'timeout'])).toEqual({
      title: '连接邮箱服务器超时或网络异常',
      text: '请检查网络后稍后重试。',
    })
    expect(pickMailboxFailure('163', ['other', 'network']).title).toBe('连接邮箱服务器超时或网络异常')
  })

  it('全是 other / null / undefined 时返回通用失败提示', () => {
    expect(pickMailboxFailure('exmail', ['other', null, undefined])).toEqual({
      title: '连接失败',
      text: '测试邮件发送失败，请检查填写内容后重试。',
    })
  })

  it('自定义邮箱非授权、非网络错误时返回列全检查项的提示（设计 3.4）', () => {
    expect(pickMailboxFailure('custom', ['other'])).toEqual({
      title: '连接失败',
      text: '测试邮件发送失败。请检查邮箱地址、密码 / 授权码和服务器设置是否正确。',
    })
  })

  it('自定义邮箱 auth 时返回自定义的提示', () => {
    const failure = pickMailboxFailure('custom', ['auth'])
    expect(failure.title).toBe('密码或授权码不正确')
    expect(failure.text).toBe('请检查邮箱地址、密码 / 授权码和服务器设置是否正确。')
  })
})
