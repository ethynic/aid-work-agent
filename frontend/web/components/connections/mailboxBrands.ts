/**
 * 连接邮箱 · 品牌表
 *
 * 纯数据 + 纯函数模块：不依赖 Vue、不发起请求。
 * 文案来源：docs/prototypes/connect-mailbox/app.js（交互设计稿）。
 *
 * kind：suffix = 账号只填前缀（域名固定）；address = 填完整地址（企业自己的域名）；
 *       custom = 自填服务器；reserved = 预留位置，参数待调研后接入。
 * serverSets 按尝试顺序排列：连接时由弹窗逐个调用保存接口，第一个成功即结束，用户不选服务器。
 */
import type { EmailSettingsConfig, MailboxEncryption } from '@/api/connectMailbox'

export type MailboxBrandKey = 'qq' | '163' | 'outlook' | 'icloud' | 'exmail' | 'qiye' | 'custom'

export type MailboxBrandKind = 'suffix' | 'address' | 'custom' | 'reserved'

export type MailboxBrandGroupKey = 'personal' | 'enterprise'

export interface MailboxServerSet {
  smtp_server: string
  smtp_port: number
  smtp_encryption: MailboxEncryption
  imap_server: string
  imap_port: number
  imap_encryption: MailboxEncryption
}

/** 连接失败时给用户看的提示 */
export interface MailboxFailure {
  title: string
  text: string
  helpLinkText?: string
}

/** 帮助说明（入口文字、操作步骤、可选补充说明、官方链接） */
export interface MailboxBrandHelp {
  steps: string[]
  extra?: string
  linkText: string
  href: string
}

export interface MailboxBrand {
  key: MailboxBrandKey
  kind: MailboxBrandKind
  group: MailboxBrandGroupKey
  name: string
  /** 卡片上的小字 */
  tileHint: string
  /** 固定域名，仅 suffix 品牌有 */
  domain?: string
  /** 服务器参数，按尝试顺序；custom 与 reserved 为空数组 */
  serverSets: MailboxServerSet[]
  /** 账号输入框 label / placeholder（reserved 品牌没有） */
  accountLabel?: string
  accountPlaceholder?: string
  /** 密码或授权码输入框 */
  secretLabel?: string
  secretPlaceholder?: string
  /** 未填写时的校验文案 */
  secretRequiredText?: string
  /** 密码框下的一句提示 */
  secretHint?: string
  /** 帮助说明入口与内容 */
  helpLabel?: string
  help?: MailboxBrandHelp
  /** 授权失败（auth）时的提示 */
  authFailure?: MailboxFailure
  /** 企业邮箱专属的一句前缀说明 */
  noticePrefix?: string
}

/** 弹窗表单的值 */
export interface MailboxConnectForm {
  /** suffix 类型是 QQ 号 / 账号名，其余是完整邮箱地址 */
  account: string
  /** 密码或授权码 */
  secret: string
  /** 仅 custom */
  smtpServer: string
  /** 仅 custom */
  imapServer: string
  /** 仅 custom，默认 '465' */
  smtpPort: string
  /** 仅 custom，默认 'ssl' */
  smtpEncryption: MailboxEncryption
  /** 仅 custom，默认 '993' */
  imapPort: string
  /** 仅 custom，默认 'ssl' */
  imapEncryption: MailboxEncryption
  /** 仅 custom，留空时取完整地址 */
  smtpUser: string
}

const BRANDS: Record<MailboxBrandKey, MailboxBrand> = {
  qq: {
    key: 'qq', kind: 'suffix', group: 'personal', name: 'QQ邮箱', domain: 'qq.com',
    tileHint: '填 QQ 号和授权码',
    serverSets: [
      { smtp_server: 'smtp.qq.com', smtp_port: 465, smtp_encryption: 'ssl', imap_server: 'imap.qq.com', imap_port: 993, imap_encryption: 'ssl' },
    ],
    accountLabel: '邮箱账号',
    accountPlaceholder: 'QQ 号',
    secretLabel: '授权码',
    secretPlaceholder: '请输入授权码',
    secretRequiredText: '请输入授权码',
    secretHint: '不是 QQ 登录密码。',
    helpLabel: '如何获取授权码？',
    help: {
      steps: [
        '在电脑浏览器登录 QQ 邮箱网页版 mail.qq.com',
        '进入「设置 → 账号」',
        '开启「IMAP/SMTP 服务」，按提示完成短信验证',
        '复制生成的 16 位授权码，填到上方',
      ],
      linkText: '查看 QQ 邮箱官方说明',
      href: 'https://service.mail.qq.com/detail/0/428',
    },
    authFailure: {
      title: '授权码不正确，或未开启 IMAP/SMTP 服务',
      text: '请确认填写的是授权码而不是登录密码。首次使用需要先在 QQ 邮箱设置中开启 IMAP/SMTP 服务。',
      helpLinkText: '查看获取步骤',
    },
  },
  '163': {
    key: '163', kind: 'suffix', group: 'personal', name: '163邮箱', domain: '163.com',
    tileHint: '填账号和授权码',
    serverSets: [
      { smtp_server: 'smtp.163.com', smtp_port: 465, smtp_encryption: 'ssl', imap_server: 'imap.163.com', imap_port: 993, imap_encryption: 'ssl' },
    ],
    accountLabel: '邮箱账号',
    accountPlaceholder: '账号名，如 zhangsan',
    secretLabel: '授权码',
    secretPlaceholder: '请输入授权码',
    secretRequiredText: '请输入授权码',
    secretHint: '不是邮箱登录密码。',
    helpLabel: '如何获取授权码？',
    help: {
      steps: [
        '在电脑浏览器登录 163 邮箱网页版 mail.163.com',
        '进入「设置 → POP3/SMTP/IMAP」',
        '开启「IMAP/SMTP 服务」，按提示完成手机验证',
        '复制生成的授权密码，填到上方',
      ],
      linkText: '查看 163 邮箱官方说明',
      href: 'http://u.163.com/cyqCpfaYf',
    },
    authFailure: {
      title: '授权码不正确，或未开启 IMAP/SMTP 服务',
      text: '请确认填写的是授权码而不是登录密码。首次使用需要先在 163 邮箱设置中开启 IMAP/SMTP 服务。',
      helpLinkText: '查看获取步骤',
    },
  },
  outlook: { key: 'outlook', kind: 'reserved', group: 'personal', name: 'Outlook', tileHint: '后续接入', serverSets: [] },
  icloud: { key: 'icloud', kind: 'reserved', group: 'personal', name: 'iCloud 邮箱', tileHint: '后续接入', serverSets: [] },
  exmail: {
    key: 'exmail', kind: 'address', group: 'enterprise', name: '腾讯企业邮箱',
    tileHint: '填邮箱地址和密码',
    serverSets: [
      { smtp_server: 'smtp.exmail.qq.com', smtp_port: 465, smtp_encryption: 'ssl', imap_server: 'imap.exmail.qq.com', imap_port: 993, imap_encryption: 'ssl' },
      { smtp_server: 'xcsmtp.exmail.qq.com', smtp_port: 465, smtp_encryption: 'ssl', imap_server: 'xcimap.exmail.qq.com', imap_port: 993, imap_encryption: 'ssl' },
    ],
    accountLabel: '邮箱地址',
    accountPlaceholder: 'name@company.com',
    secretLabel: '密码 / 客户端专用密码',
    secretPlaceholder: '邮箱密码，或客户端专用密码',
    secretRequiredText: '请输入密码或客户端专用密码',
    secretHint: '已开启「安全登录」的邮箱，需要在邮箱设置里生成客户端专用密码。',
    helpLabel: '怎么填写和开通？',
    help: {
      steps: [
        '在电脑浏览器登录企业邮箱网页版 exmail.qq.com，或从企业微信进入邮箱',
        '进入「设置 → 收发信设置」，开启「IMAP/SMTP 服务」',
        '没有开启「安全登录」：密码处填邮箱密码',
        '已开启「安全登录」：进入「设置 → 邮箱绑定」，生成客户端专用密码，填到密码处',
      ],
      extra: '怎么开通：第 2 步勾选不了时，请公司邮箱管理员到企业微信管理端「协作 → 邮件 → 安全管理 → 客户端访问权限」开启。',
      linkText: '查看企业微信邮箱官方说明',
      href: 'https://open.work.weixin.qq.com/help2/pc/19886',
    },
    authFailure: {
      title: '已尝试所有服务器，请检查填写内容',
      text: '通常是密码不正确，或邮箱还没有开通客户端收发权限。开启了「安全登录」的邮箱要填「客户端专用密码」，不是登录密码；「收发信设置」里勾选不了服务时，需要公司邮箱管理员开启客户端访问权限。',
      helpLinkText: '查看填写和开通步骤',
    },
    noticePrefix: '系统会自动匹配邮箱服务器，不需要你选择。',
  },
  qiye: {
    key: 'qiye', kind: 'address', group: 'enterprise', name: '网易企业邮箱',
    tileHint: '填邮箱地址和授权码',
    // 付费版按邮箱所在服务器分华北、华东两套地址；免费版（ym.163.com）加密端口 994。
    // 连接时按顺序依次尝试：付费华北 → 付费华东 → 免费版，成功哪套用哪套。
    serverSets: [
      { smtp_server: 'smtp.qiye.163.com', smtp_port: 465, smtp_encryption: 'ssl', imap_server: 'imap.qiye.163.com', imap_port: 993, imap_encryption: 'ssl' },
      { smtp_server: 'smtphz.qiye.163.com', smtp_port: 465, smtp_encryption: 'ssl', imap_server: 'imaphz.qiye.163.com', imap_port: 993, imap_encryption: 'ssl' },
      { smtp_server: 'smtp.ym.163.com', smtp_port: 994, smtp_encryption: 'ssl', imap_server: 'imap.ym.163.com', imap_port: 993, imap_encryption: 'ssl' },
    ],
    accountLabel: '邮箱地址',
    accountPlaceholder: 'name@company.com',
    secretLabel: '授权码 / 密码',
    secretPlaceholder: '客户端授权码，或邮箱密码',
    secretRequiredText: '请输入授权码或密码',
    secretHint: '付费版填「客户端授权码」，免费版填邮箱密码。',
    helpLabel: '怎么填写和开通？',
    help: {
      steps: [
        '在电脑浏览器登录网易企业邮箱网页版 qy.163.com',
        '进入「设置 → 账户与安全 → 客户端设置」',
        '生成客户端授权码并复制（只显示一次），填到密码处',
        '免费企业邮箱（ym.163.com）没有授权码，密码处直接填邮箱密码',
      ],
      extra: '怎么开通：提示没有权限时，请公司邮箱管理员在邮箱后台为这个账号开启 IMAP、SMTP 权限。',
      linkText: '查看网易企业邮箱官方说明',
      href: 'https://office.163.com/helpCenter/mail/d/2057123645218729986.html',
    },
    authFailure: {
      title: '已尝试所有服务器，请检查填写内容',
      text: '通常是授权码或密码不正确，或邮箱还没有开通客户端收发权限。付费版要填「客户端授权码」，免费版填邮箱密码；权限需要公司邮箱管理员在邮箱后台为这个账号开启。',
      helpLinkText: '查看填写和开通步骤',
    },
    noticePrefix: '系统会自动匹配邮箱服务器，不需要你选择。',
  },
  custom: {
    key: 'custom', kind: 'custom', group: 'enterprise', name: '自定义邮箱',
    tileHint: '其他企业邮箱',
    serverSets: [],
    accountLabel: '邮箱地址',
    accountPlaceholder: 'name@company.com',
    secretLabel: '密码 / 授权码',
    secretPlaceholder: '邮箱密码，或邮箱提供的授权码',
    authFailure: {
      title: '密码或授权码不正确',
      text: '请检查邮箱地址、密码 / 授权码和服务器设置是否正确。',
    },
  },
}

const GROUPS: Array<{ key: MailboxBrandGroupKey; title: string; desc?: string; brands: MailboxBrandKey[] }> = [
  { key: 'personal', title: '个人邮箱', brands: ['qq', '163', 'outlook', 'icloud'] },
  {
    key: 'enterprise',
    title: '企业邮箱',
    desc: '公司邮箱一般使用企业自己的域名。不确定是哪家，可以看平时登录邮箱的网址。',
    brands: ['exmail', 'qiye', 'custom'],
  },
]

/** 页面上要显示的分组（已剔除预留品牌） */
export function visibleMailboxGroups(): Array<{ key: MailboxBrandGroupKey; title: string; desc?: string; brands: MailboxBrand[] }> {
  return GROUPS.map(({ key, title, desc, brands }) => ({
    key,
    title,
    desc,
    brands: brands
      .map(brandKey => BRANDS[brandKey])
      .filter(brand => brand.kind !== 'reserved'),
  }))
}

export function getMailboxBrand(key: string): MailboxBrand | undefined {
  return (BRANDS as Record<string, MailboxBrand | undefined>)[key]
}

/** 把用户填的账号补成完整地址：suffix 类型且输入不含 @ 时补上固定域名 */
export function resolveMailboxAddress(brand: MailboxBrand, account: string): string {
  const raw = account.trim()
  if (brand.kind !== 'suffix' || raw.includes('@')) return raw
  return raw ? `${raw}@${brand.domain}` : ''
}

/** 按尝试顺序生成 9 项配置：单服务器品牌 1 项，腾讯企业邮箱 2 项，网易企业邮箱 3 项 */
export function buildMailboxConfigs(brand: MailboxBrand, form: MailboxConnectForm): EmailSettingsConfig[] {
  const address = resolveMailboxAddress(brand, form.account)
  const smtpUser = form.smtpUser.trim() || address

  if (brand.kind === 'custom') {
    return [{
      email_address: address,
      smtp_server: form.smtpServer.trim(),
      smtp_port: Number(form.smtpPort.trim()),
      smtp_user: smtpUser,
      smtp_password: form.secret.trim(),
      smtp_encryption: form.smtpEncryption,
      imap_server: form.imapServer.trim(),
      imap_port: Number(form.imapPort.trim()),
      imap_encryption: form.imapEncryption,
    }]
  }

  return brand.serverSets.map(serverSet => ({
    email_address: address,
    smtp_server: serverSet.smtp_server,
    smtp_port: serverSet.smtp_port,
    smtp_user: smtpUser,
    smtp_password: form.secret.trim(),
    smtp_encryption: serverSet.smtp_encryption,
    imap_server: serverSet.imap_server,
    imap_port: serverSet.imap_port,
    imap_encryption: serverSet.imap_encryption,
  }))
}

/** 根据已保存的配置判断是哪个品牌：smtp_server 与哪个品牌的服务器一致就是哪个，都不是则为 custom */
export function detectMailboxBrand(config: EmailSettingsConfig): MailboxBrandKey {
  const smtpServer = config.smtp_server.trim().toLowerCase()
  for (const key of ['qq', '163', 'exmail', 'qiye'] as const) {
    if (BRANDS[key].serverSets.some(serverSet => serverSet.smtp_server.toLowerCase() === smtpServer)) {
      return key
    }
  }
  return 'custom'
}

const NETWORK_FAILURE: MailboxFailure = {
  title: '连接邮箱服务器超时或网络异常',
  text: '请检查网络后稍后重试。',
}

const GENERIC_FAILURE: MailboxFailure = {
  title: '连接失败',
  text: '测试邮件发送失败，请检查填写内容后重试。',
}

/** 设计文档 3.4：自定义邮箱的通用失败提示要列全要检查的三样内容 */
const CUSTOM_FAILURE: MailboxFailure = {
  title: '连接失败',
  text: '测试邮件发送失败。请检查邮箱地址、密码 / 授权码和服务器设置是否正确。',
}

/** 全部尝试失败后选一条提示：任意一次是 auth → 该品牌的「填写内容有误」提示；否则有 connect/timeout/network → 网络提示；其余 → 通用失败提示（自定义邮箱单独一条） */
export function pickMailboxFailure(brandKey: MailboxBrandKey, errorCodes: Array<string | null | undefined>): MailboxFailure {
  if (errorCodes.includes('auth')) {
    const authFailure = getMailboxBrand(brandKey)?.authFailure
    if (authFailure) return authFailure
  }
  if (errorCodes.some(code => code === 'connect' || code === 'timeout' || code === 'network')) {
    return NETWORK_FAILURE
  }
  return brandKey === 'custom' ? CUSTOM_FAILURE : GENERIC_FAILURE
}
