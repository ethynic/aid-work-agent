/* 连接邮箱 · 交互设计稿
   纯静态示例数据，不调用接口：顶部导航条切换场景，页面和弹窗可以直接操作。 */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pad = (n) => String(n).padStart(2, '0');
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  };
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  /* ---------- 图标 ---------- */
  const PATHS = {
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="M6 18L18 6M6 6l12 12"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    chevRight: '<path d="M9 5l7 7-7 7"/>',
    chevDown: '<path d="M6 9l6 6 6-6"/>',
    collapse: '<path d="M11 19l-7-7 7-7M20 19l-7-7 7-7"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    alert: '<path d="M12 9v4M12 16.5h.01M10.3 3.9L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.8 9.6a2.3 2.3 0 014.5.6c0 1.5-2.3 1.8-2.3 3.3M12 16.6h.01"/>',
    refresh: '<path d="M20 11a8 8 0 00-14.9-3M4 4v4h4M4 13a8 8 0 0014.9 3M20 20v-4h-4"/>',
    monitor: '<path d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>',
    grid: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
    chart: '<path d="M3 3v18h18M8 16v-5M13 16V8M18 16v-3"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>',
    plug: '<path d="M9 7V3M15 7V3M9 21v-4M15 21v-4M5 12H3M21 12h-2M7 9h10a2 2 0 012 2v2a2 2 0 01-2 2H7a2 2 0 01-2-2v-2a2 2 0 012-2z"/>',
    sliders: '<path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>',
    signal: '<path d="M5 12.55a11 11 0 0114 0M1.42 9a16 16 0 0121.16 0M8.53 16.11a6 6 0 016.95 0M12 20h.01"/>',
    doc: '<path d="M5 4h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1zM8 9h8M8 13h8M8 17h5"/>',
    chat: '<path d="M8 10h.01M12 10h.01M16 10h.01M20 11.5c0 4.1-3.6 7.5-8 7.5a8.9 8.9 0 01-3-.5L4 20l1.4-4.2A7 7 0 014 11.5C4 7.4 7.6 4 12 4s8 3.4 8 7.5z"/>',
    bot: '<path d="M12 3a8 8 0 018 8v6H4v-6a8 8 0 018-8zM9 21h6M9 11h6"/>',
    mail: '<path d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>',
    link: '<path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/>',
    dots: '<path d="M6 12h.01M12 12h.01M18 12h.01"/>',
  };
  const icon = (name, cls = '') =>
    `<svg${cls ? ` class="${cls}"` : ''} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name]}</svg>`;

  /* 品牌图标为示意稿：开发时替换为品牌官方图标素材 */
  const envelope = (color) =>
    `<rect x="6.5" y="11" width="24" height="17" rx="2.6" fill="#fff"/>
     <path d="M8.1 13.2 18.5 20.6 28.9 13.2" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>`;
  /* 右下角的楼房角标：表示「企业邮箱」 */
  const buildingBadge = (color) =>
    `<circle cx="31.5" cy="29.5" r="7" fill="#fff"/>
     <path d="M29 32.4V26.6h5v5.8M27.8 32.4h7.4" fill="none" stroke="${color}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
     <path d="M30.5 28.4h.01M32.5 28.4h.01M30.5 30.3h.01M32.5 30.3h.01" stroke="${color}" stroke-width="1.4" stroke-linecap="round"/>`;

  function brandIcon(key, size) {
    const attr = `class="brand-icon" width="${size}" height="${size}" viewBox="0 0 40 40" aria-hidden="true" focusable="false"`;
    if (key === 'qq') {
      return `<svg ${attr}>
        <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#38B6F0"/>
        <rect x="6.5" y="10.5" width="27" height="19.5" rx="3" fill="#fff"/>
        <path d="M8.2 13 20 21.6 31.8 13" fill="none" stroke="#38B6F0" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="31.5" cy="29.5" r="7" fill="#fff" stroke="#38B6F0"/>
        <ellipse cx="31.5" cy="29.9" rx="3.7" ry="4.3" fill="#262626"/>
        <ellipse cx="31.5" cy="32" rx="2.1" ry="1.8" fill="#fff"/>
        <circle cx="30.4" cy="28.1" r=".85" fill="#fff"/><circle cx="32.6" cy="28.1" r=".85" fill="#fff"/>
        <circle cx="30.55" cy="28.2" r=".4" fill="#262626"/><circle cx="32.45" cy="28.2" r=".4" fill="#262626"/>
        <path d="M30.6 29.3h1.8l-.9 1.1z" fill="#F7A531"/>
        <path d="M29.2 30.5h4.6v1.1h-4.6z" fill="#E5413B"/>
      </svg>`;
    }
    if (key === '163') {
      return `<svg ${attr}>
        <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#DD4037"/>
        <rect x="11.5" y="8" width="17" height="11.5" rx="1.8" fill="none" stroke="#fff" stroke-width="1.6"/>
        <path d="M12.4 9.2 20 14.4l7.6-5.2" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        <text x="20" y="31.2" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="12.5" font-weight="700" fill="#fff" letter-spacing="-.2">163</text>
      </svg>`;
    }
    if (key === 'outlook') {
      return `<svg ${attr}>
        <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#0F6CBD"/>
        <text x="19" y="27.8" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="20" font-weight="700" fill="#fff">O</text>
        <rect x="26.5" y="24.5" width="10" height="7.5" rx="1.4" fill="#fff"/>
        <path d="M27.2 25.4 31.5 28.8 35.8 25.4" fill="none" stroke="#0F6CBD" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
    }
    if (key === 'icloud') {
      return `<svg ${attr}>
        <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#5AA2F0"/>
        ${envelope('#5AA2F0')}
      </svg>`;
    }
    if (key === 'exmail') {
      return `<svg ${attr}>
        <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#2E6BE6"/>
        ${envelope('#2E6BE6')}
        ${buildingBadge('#2E6BE6')}
      </svg>`;
    }
    if (key === 'qiye') {
      return `<svg ${attr}>
        <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#A62B25"/>
        ${envelope('#A62B25')}
        ${buildingBadge('#A62B25')}
      </svg>`;
    }
    return `<svg ${attr}>
      <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#F3F4F6" stroke="#E5E7EB"/>
      <rect x="8" y="10.5" width="19" height="14" rx="2.4" fill="#fff" stroke="#4B5563" stroke-width="1.7"/>
      <path d="M9 12.2 17.5 18.4 26 12.2" fill="none" stroke="#4B5563" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="28.5" cy="28.5" r="7" fill="#fff" stroke="#D1D5DB"/>
      <path d="M28.5 25.3v6.4M25.3 28.5h6.4" stroke="#4B5563" stroke-width="1.7" stroke-linecap="round"/>
    </svg>`;
  }

  /* ---------- 邮箱品牌 ----------
     kind：suffix = 账号只填前缀（域名固定）；address = 填完整地址（企业自己的域名）；
           custom = 自填服务器；reserved = 预留位置，参数待调研。
     servers 只作设计记录（界面不展示）：连接时由系统带入。 */
  const ENCRYPTION_OPTIONS = [
    { value: 'ssl', label: 'SSL（465/993）' },
    { value: 'tls', label: 'STARTTLS（587/143）' },
    { value: 'none', label: '无加密（不推荐）' },
  ];
  const BRANDS = {
    qq: {
      key: 'qq', kind: 'suffix', name: 'QQ邮箱', domain: 'qq.com',
      tileHint: '填 QQ 号和授权码',
      accountPlaceholder: 'QQ 号',
      codeHint: '不是 QQ 登录密码。',
      helpLabel: '如何获取授权码？',
      help: {
        steps: [
          '在电脑浏览器登录 QQ 邮箱网页版 mail.qq.com',
          '进入「设置 → 账号」',
          '开启「IMAP/SMTP 服务」，按提示完成短信验证',
          '复制生成的 16 位授权码，填到上方',
        ],
        link: '查看 QQ 邮箱官方说明',
        href: 'https://service.mail.qq.com/detail/0/428',
      },
      servers: { smtp: 'smtp.qq.com', smtpPort: 465, imap: 'imap.qq.com', imapPort: 993, encryption: 'ssl' },
    },
    '163': {
      key: '163', kind: 'suffix', name: '163邮箱', domain: '163.com',
      tileHint: '填账号和授权码',
      accountPlaceholder: '账号名，如 zhangsan',
      codeHint: '不是邮箱登录密码。',
      helpLabel: '如何获取授权码？',
      help: {
        steps: [
          '在电脑浏览器登录 163 邮箱网页版 mail.163.com',
          '进入「设置 → POP3/SMTP/IMAP」',
          '开启「IMAP/SMTP 服务」，按提示完成手机验证',
          '复制生成的授权密码，填到上方',
        ],
        link: '查看 163 邮箱官方说明',
        href: 'http://u.163.com/cyqCpfaYf',
      },
      servers: { smtp: 'smtp.163.com', smtpPort: 465, imap: 'imap.163.com', imapPort: 993, encryption: 'ssl' },
    },
    outlook: { key: 'outlook', kind: 'reserved', name: 'Outlook', tileHint: '后续接入' },
    icloud: { key: 'icloud', kind: 'reserved', name: 'iCloud 邮箱', tileHint: '后续接入' },
    exmail: {
      key: 'exmail', kind: 'address', name: '腾讯企业邮箱', tileHint: '填邮箱地址和密码',
      credentialLabel: '密码 / 客户端专用密码',
      credentialPlaceholder: '邮箱密码，或客户端专用密码',
      credentialHint: '已开启「安全登录」的邮箱，需要在邮箱设置里生成客户端专用密码。',
      codeRequiredText: '请输入密码或客户端专用密码',
      helpLabel: '怎么填写和开通？',
      help: {
        steps: [
          '在电脑浏览器登录企业邮箱网页版 exmail.qq.com，或从企业微信进入邮箱',
          '进入「设置 → 收发信设置」，开启「IMAP/SMTP 服务」',
          '没有开启「安全登录」：密码处填邮箱密码',
          '已开启「安全登录」：进入「设置 → 邮箱绑定」，生成客户端专用密码，填到密码处',
        ],
        extra: '怎么开通：第 2 步勾选不了时，请公司邮箱管理员到企业微信管理端「协作 → 邮件 → 安全管理 → 客户端访问权限」开启。',
        link: '查看企业微信邮箱官方说明',
        href: 'https://open.work.weixin.qq.com/help2/pc/19886',
      },
      note: 6,
      noticePrefix: '系统会自动匹配邮箱服务器，不需要你选择。',
      /* 通用和信创两套服务器，连接时由系统依次尝试，成功哪套用哪套 */
      servers: {
        standard: { smtp: 'smtp.exmail.qq.com', smtpPort: 465, imap: 'imap.exmail.qq.com', imapPort: 993, encryption: 'ssl' },
        xinchuang: { smtp: 'xcsmtp.exmail.qq.com', smtpPort: 465, imap: 'xcimap.exmail.qq.com', imapPort: 993, encryption: 'ssl' },
      },
    },
    qiye: {
      key: 'qiye', kind: 'address', name: '网易企业邮箱', tileHint: '填邮箱地址和授权码',
      credentialLabel: '授权码 / 密码',
      credentialPlaceholder: '客户端授权码，或邮箱密码',
      credentialHint: '付费版填「客户端授权码」，免费版填邮箱密码。',
      codeRequiredText: '请输入授权码或密码',
      helpLabel: '怎么填写和开通？',
      help: {
        steps: [
          '在电脑浏览器登录网易企业邮箱网页版 qy.163.com',
          '进入「设置 → 账户与安全 → 客户端设置」',
          '生成客户端授权码并复制（只显示一次），填到密码处',
          '免费企业邮箱（ym.163.com）没有授权码，密码处直接填邮箱密码',
        ],
        extra: '怎么开通：提示没有权限时，请公司邮箱管理员在邮箱后台为这个账号开启 IMAP、SMTP 权限。',
        link: '查看网易企业邮箱官方说明',
        href: 'https://office.163.com/helpCenter/mail/d/2057123645218729986.html',
      },
      note: 7,
      noticePrefix: '系统会自动匹配邮箱服务器，不需要你选择。',
      /* 付费版按邮箱所在服务器分华北、华东两套地址；免费版（ym.163.com）是另一套，加密端口 994
         连接时由系统依次尝试：付费华北 → 付费华东 → 免费版，成功哪套用哪套 */
      servers: {
        'paid-hb': { smtp: 'smtp.qiye.163.com', smtpPort: 465, imap: 'imap.qiye.163.com', imapPort: 993, encryption: 'ssl' },
        'paid-hd': { smtp: 'smtphz.qiye.163.com', smtpPort: 465, imap: 'imaphz.qiye.163.com', imapPort: 993, encryption: 'ssl' },
        free: { smtp: 'smtp.ym.163.com', smtpPort: 994, imap: 'imap.ym.163.com', imapPort: 993, encryption: 'ssl' },
      },
    },
    custom: { key: 'custom', kind: 'custom', name: '自定义邮箱', domain: '', tileHint: '其他企业邮箱' },
  };
  const GROUPS = [
    { key: 'personal', title: '个人邮箱', brands: ['qq', '163', 'outlook', 'icloud'] },
    {
      key: 'enterprise', title: '企业邮箱', brands: ['exmail', 'qiye', 'custom'],
      desc: '公司邮箱一般使用企业自己的域名。不确定是哪家，可以看平时登录邮箱的网址。',
    },
  ];

  /* 连接失败的提示文案（示例：最常见的凭据错误） */
  const FAILURE = {
    qq: {
      title: '授权码不正确，或未开启 IMAP/SMTP 服务',
      text: '请确认填写的是授权码而不是登录密码。首次使用需要先在 QQ 邮箱设置中开启 IMAP/SMTP 服务。',
    },
    '163': {
      title: '授权码不正确，或未开启 IMAP/SMTP 服务',
      text: '请确认填写的是授权码而不是登录密码。首次使用需要先在 163 邮箱设置中开启 IMAP/SMTP 服务。',
    },
    exmail: {
      title: '已尝试所有服务器，请检查填写内容',
      text: '通常是密码不正确，或邮箱还没有开通客户端收发权限。开启了「安全登录」的邮箱要填「客户端专用密码」，不是登录密码；「收发信设置」里勾选不了服务时，需要公司邮箱管理员开启客户端访问权限。',
      link: '查看填写和开通步骤',
    },
    qiye: {
      title: '已尝试所有服务器，请检查填写内容',
      text: '通常是授权码或密码不正确，或邮箱还没有开通客户端收发权限。付费版要填「客户端授权码」，免费版填邮箱密码；权限需要公司邮箱管理员在邮箱后台为这个账号开启。',
      link: '查看填写和开通步骤',
    },
    custom: {
      title: '测试邮件发送失败',
      text: '请检查邮箱地址、密码 / 授权码和服务器设置是否正确。',
    },
  };

  const NOTES = [
    { n: 1, title: '入口', scene: 'entry', text: '连接中心子菜单新增「连接邮箱」，全体员工可见，排在「外部系统」之后。原「设置 → 邮箱设置」页签移除。' },
    { n: 2, title: '品牌入口', scene: 'empty', text: '分成「个人邮箱」「企业邮箱」两组。点开品牌只需填账号和密码或授权码，服务器地址、端口、加密方式由系统带入，不再让用户填 9 项。' },
    { n: 3, title: '预留位置', scene: 'empty', text: 'Outlook、iCloud 邮箱先占住位置，参数待调研后接入，原型里不可点击。' },
    { n: 4, title: '账号只填前缀', scene: 'qq', text: 'QQ邮箱、163邮箱的后缀固定显示，用户只输入 QQ 号或账号名，直接填完整地址也可以。填了其他域名时提示改用对应品牌或自定义邮箱。' },
    { n: 5, title: '授权码而不是密码', scene: 'qq-help', text: 'QQ、163 登录第三方客户端用的是授权码，不是登录密码。字段写成「授权码」并附获取步骤，避免用户填登录密码后连接失败。' },
    { n: 6, title: '腾讯企业邮箱', scene: 'exmail', text: '地址用公司自己的域名，所以填完整邮箱地址。密码处填邮箱密码；开启了「安全登录」的邮箱要填「客户端专用密码」。通用、信创两套服务器由系统依次尝试，不让用户选。' },
    { n: 7, title: '网易企业邮箱合并为一个入口', scene: 'qiye', text: '付费版华北、付费版华东、免费版三套服务器由系统依次尝试，成功哪套用哪套，不让用户判断自己是哪个版本。付费版必须用客户端授权码，免费版直接填密码。' },
    { n: 8, title: '所有服务器都失败时', scene: 'exmail-failed', text: '同一品牌的服务器全部试过仍失败，说明填写内容有误或邮箱没有开通权限。提示里只讲两件事：怎么填（密码还是授权码），以及需要公司邮箱管理员在后台开通什么。不出现「选服务器」这类用户答不上来的问题。' },
    { n: 9, title: '连接校验', scene: 'connecting', text: '沿用现有做法：点「连接」后向该邮箱发一封测试邮件，发送成功才保存。连接中不能关闭弹窗。' },
    { n: 10, title: '更换提示', scene: 'replace', text: '每个账号连接 1 个邮箱。已有连接时再选其他邮箱，弹窗顶部提示会替换当前邮箱。' },
    { n: 11, title: '已连接', scene: 'connected', text: '显示邮箱地址、品牌、连接时间和状态。密码和授权码加密保存、不回显，更换时用「重新连接」。' },
    { n: 12, title: '断开连接', scene: 'disconnect', text: '沿用现有页面的确认方式（浏览器确认框），文案说明断开后的影响。' },
    { n: 13, title: '自定义邮箱', scene: 'custom-advanced', text: '默认只填 4 项：邮箱地址、密码 / 授权码、发件服务器、收件服务器。端口、加密方式、SMTP 用户名收进「高级设置」。' },
    { n: 14, title: '手机端入口保持现状', scene: '', text: '现有手机端侧栏不显示连接中心入口，也不显示设置入口，本次不改变。390 宽度只用于检查页面和弹窗的排版。' },
  ];

  /* ---------- 状态 ---------- */
  const state = { scene: '', view: 'ready', mailbox: null, modal: null, flyout: false };

  const frame = $('#frame');
  const app = $('#app');
  const page = $('#page');
  const overlay = $('#connect-overlay');
  const modal = $('#connect-modal');
  const body = $('#connect-body');
  const flyout = $('#flyout-connection');
  const toast = $('#toast');

  /* ---------- 页面 ---------- */
  function tileTemplate(key, withNote) {
    const b = BRANDS[key];
    const reserved = b.kind === 'reserved';
    const extra = reserved ? ` aria-disabled="true" title="预留位置，参数调研后接入"${withNote ? ' data-note="3"' : ''}` : '';
    return `<button type="button" class="brand-tile${reserved ? ' is-reserved' : ''}" data-brand="${key}" aria-label="${reserved ? `${b.name}，预留位置` : `连接${b.name}`}"${extra}>
      ${brandIcon(key, 40)}
      <span class="brand-tile-text"><span class="brand-tile-name">${b.name}</span><span class="brand-tile-hint">${b.tileHint}</span></span>
      ${reserved ? '<span class="tile-tag" aria-hidden="true">预留</span>' : ''}
    </button>`;
  }

  function tiles() {
    return `<div class="brand-groups" data-note="2">${GROUPS.map((g) => `
      <section class="brand-group">
        <div class="brand-group-head">
          <h3 class="brand-group-title">${g.title}</h3>
          ${g.desc ? `<p class="brand-group-desc">${g.desc}</p>` : ''}
        </div>
        <div class="brand-grid" role="group" aria-label="${g.title}">${g.brands.map((key) => tileTemplate(key, key === 'outlook')).join('')}</div>
      </section>`).join('')}</div>`;
  }

  function pageTemplate() {
    if (state.view === 'loading') {
      const group = (count) => `<div class="brand-grid">${'<div class="brand-tile is-skeleton"><span class="skeleton sk-icon"></span><span class="sk-lines"><span class="skeleton sk-line"></span><span class="skeleton sk-line sk-short"></span></span></div>'.repeat(count)}</div>`;
      return `<div class="page-inner"><section class="card"><div class="skeleton sk-title"></div><div class="skeleton sk-text"></div><div class="brand-groups"><div class="brand-group"><div class="skeleton sk-text"></div>${group(4)}</div><div class="brand-group"><div class="skeleton sk-text"></div>${group(3)}</div></div></section></div>
        <p class="sr-only" role="status">正在加载邮箱信息</p>`;
    }
    if (state.view === 'error') {
      return `<div class="page-inner"><section class="card">
        <div class="card-head"><h2>我的邮箱</h2></div>
        <div class="state-block">${icon('alert', 'state-icon')}<p class="state-title">邮箱信息加载失败</p><p class="state-text">请检查网络后重试。</p>
          <button type="button" class="btn btn-secondary" id="btn-retry">${icon('refresh')}重试</button></div>
      </section></div>`;
    }
    const mb = state.mailbox;
    if (!mb) {
      return `<div class="page-inner"><section class="card">
        <div class="card-head">
          <h2>我的邮箱</h2>
          <p>连接后，数字员工可以代你收发邮件。选择邮箱类型，只需填写账号和密码或授权码。</p>
        </div>
        ${tiles()}
        <p class="card-foot">每个账号连接 1 个邮箱，连接新的会替换当前的。密码和授权码加密保存，保存后不再显示。</p>
      </section></div>`;
    }
    return `<div class="page-inner">
      <section class="card">
        <div class="card-head">
          <h2>我的邮箱</h2>
          <p>数字员工发邮件、读邮件时使用这个邮箱。</p>
        </div>
        <div class="mailbox" data-note="11">
          ${brandIcon(mb.brand, 48)}
          <div class="mailbox-body">
            <p class="mailbox-address">${esc(mb.address)}</p>
            <p class="mailbox-meta">${BRANDS[mb.brand].name} · ${mb.since} 连接</p>
          </div>
          <span class="badge badge-success">${icon('check')}已连接</span>
          <div class="mailbox-actions">
            <button type="button" class="btn btn-secondary" id="btn-reconnect">重新连接</button>
            <button type="button" class="btn btn-danger-ghost" id="btn-disconnect" data-note="12">断开连接</button>
          </div>
        </div>
      </section>
      <section class="card">
        <div class="card-head">
          <h2>更换邮箱</h2>
          <p>连接新邮箱后会替换当前邮箱。</p>
        </div>
        ${tiles()}
      </section>
    </div>`;
  }

  function renderPage() {
    page.innerHTML = pageTemplate();
    page.setAttribute('aria-busy', String(state.view === 'loading'));
  }

  function askDisconnect() {
    const mb = state.mailbox;
    if (!mb) return;
    const ok = window.confirm(`断开邮箱连接？\n\n断开后，数字员工将不能再用 ${mb.address} 收发邮件。`);
    if (!ok) return;
    state.mailbox = null;
    renderPage();
    showToast('已断开邮箱连接');
  }

  page.addEventListener('click', (e) => {
    const tile = e.target.closest('[data-brand]');
    if (tile) {
      const brand = tile.dataset.brand;
      if (BRANDS[brand].kind === 'reserved') { showToast(`「${BRANDS[brand].name}」是预留位置，参数调研后接入`); return; }
      openModal(brand, state.mailbox && state.mailbox.brand === brand ? { mode: 'reconnect' } : {});
      return;
    }
    if (e.target.closest('#btn-reconnect')) { openModal(state.mailbox.brand, { mode: 'reconnect' }); return; }
    if (e.target.closest('#btn-disconnect')) { askDisconnect(); return; }
    if (e.target.closest('#btn-retry')) {
      state.view = 'loading';
      renderPage();
      setTimeout(() => { if (state.view === 'loading') { state.view = 'ready'; renderPage(); } }, 800);
    }
  });

  /* ---------- 弹窗 ---------- */
  const replaceNotice = () =>
    `<div class="notice notice-warning" id="replace-notice" data-note="10" hidden>${icon('alert')}<span>连接成功后将替换当前已连接的 <b id="replace-address"></b>。</span></div>`;
  const testNotice = (prefix = '') =>
    `<div class="notice notice-info" data-note="9">${icon('info')}<span>${prefix}点击「连接」后会向该邮箱发送一封测试邮件，发送成功即连接成功。</span></div>`;
  const errorNotice = () => `<div class="notice notice-danger" id="connect-error" role="alert" data-note="8" hidden></div>`;
  const noteAttr = (n) => (n ? ` data-note="${n}"` : '');

  /* 三种弹窗正文都把失败提示放在最上面：窗口高度不足或展开步骤后，原因和处理办法仍然看得见 */

  /* 域名固定的品牌（QQ邮箱、163邮箱）：账号 + 授权码 */
  function suffixBody(m, b) {
    return `${errorNotice()}
      ${replaceNotice()}
      <div class="field" data-note="4">
        <label class="field-label" for="f-account">邮箱账号</label>
        <div class="affix">
          <input id="f-account" class="input" autocomplete="off" spellcheck="false" placeholder="${b.accountPlaceholder}" value="${esc(m.values.account)}" aria-describedby="account-feedback">
          <span class="affix-suffix" id="account-suffix">@${b.domain}</span>
        </div>
        <p class="field-feedback" id="account-feedback" hidden></p>
      </div>
      <div class="field" data-note="5">
        <label class="field-label" for="f-code">授权码</label>
        <input id="f-code" class="input" type="password" autocomplete="new-password" placeholder="请输入授权码" value="${esc(m.values.code)}" aria-describedby="code-feedback">
        <p class="field-feedback" id="code-feedback" hidden></p>
        <p class="hint">${b.codeHint}</p>
        <button type="button" class="help-toggle" id="toggle-help" aria-expanded="false" aria-controls="help-steps">${icon('help')}<span>${b.helpLabel}</span>${icon('chevDown', 'chev')}</button>
        <div class="help-body" id="help-steps" hidden>
          <ol>${b.help.steps.map((s) => `<li>${s}</li>`).join('')}</ol>
          <a href="${b.help.href}" target="_blank" rel="noopener noreferrer">${b.help.link}</a>
        </div>
      </div>
      ${testNotice()}`;
  }

  /* 地址用企业域名的品牌（腾讯企业邮箱、网易企业邮箱）：完整地址 + 密码或授权码。
     同一品牌有多套服务器，由系统依次尝试，界面不提供选择 */
  function addressBody(m, b) {
    return `${errorNotice()}
      ${replaceNotice()}
      <div class="field"${noteAttr(b.note)}>
        <label class="field-label" for="f-address">邮箱地址</label>
        <input id="f-address" class="input" autocomplete="off" spellcheck="false" placeholder="name@company.com" value="${esc(m.values.address)}" aria-describedby="address-feedback">
        <p class="field-feedback" id="address-feedback" hidden></p>
      </div>
      <div class="field">
        <label class="field-label" for="f-code">${b.credentialLabel}</label>
        <input id="f-code" class="input" type="password" autocomplete="new-password" placeholder="${b.credentialPlaceholder}" value="${esc(m.values.code)}" aria-describedby="code-feedback">
        <p class="field-feedback" id="code-feedback" hidden></p>
        <p class="hint">${b.credentialHint}</p>
        <button type="button" class="help-toggle" id="toggle-help" aria-expanded="false" aria-controls="help-steps">${icon('help')}<span>${b.helpLabel}</span>${icon('chevDown', 'chev')}</button>
        <div class="help-body" id="help-steps" hidden>
          <ol>${b.help.steps.map((s) => `<li>${s}</li>`).join('')}</ol>
          ${b.help.extra ? `<p class="help-extra">${b.help.extra}</p>` : ''}
          <a href="${b.help.href}" target="_blank" rel="noopener noreferrer">${b.help.link}</a>
        </div>
      </div>
      ${testNotice(b.noticePrefix)}`;
  }

  function customBody(m) {
    const v = m.values;
    const options = (selected) => ENCRYPTION_OPTIONS.map((o) => `<option value="${o.value}"${o.value === selected ? ' selected' : ''}>${o.label}</option>`).join('');
    const field = (key, label, control) => `<div class="field"><label class="field-label" for="f-${key}">${label}</label>${control}<p class="field-feedback" id="${key}-feedback" hidden></p></div>`;
    return `${errorNotice()}
      ${replaceNotice()}
      ${field('address', '邮箱地址', `<input id="f-address" class="input" autocomplete="off" spellcheck="false" placeholder="name@company.com" value="${esc(v.address)}" aria-describedby="address-feedback">`)}
      ${field('code', '密码 / 授权码', `<input id="f-code" class="input" type="password" autocomplete="new-password" placeholder="邮箱密码，或邮箱提供的授权码" value="${esc(v.code)}" aria-describedby="code-feedback">`)}
      <div class="server-grid">
        ${field('smtp', '发件服务器（SMTP）', `<input id="f-smtp" class="input" autocomplete="off" spellcheck="false" placeholder="smtp.example.com" value="${esc(v.smtp)}" aria-describedby="smtp-feedback">`)}
        ${field('imap', '收件服务器（IMAP）', `<input id="f-imap" class="input" autocomplete="off" spellcheck="false" placeholder="imap.example.com" value="${esc(v.imap)}" aria-describedby="imap-feedback">`)}
      </div>
      <div class="disclosure" data-note="13">
        <button type="button" class="disclosure-toggle" id="toggle-adv" aria-expanded="false" aria-controls="adv-body">
          ${icon('chevRight', 'chev')}<span>高级设置</span><span class="disclosure-desc">端口、加密方式、SMTP 用户名，通常无需修改</span>
        </button>
        <div class="disclosure-body" id="adv-body" hidden>
          <div class="adv-grid">
            ${field('smtp-port', 'SMTP 端口', `<input id="f-smtp-port" class="input" inputmode="numeric" value="${esc(v.smtpPort)}" aria-describedby="smtp-port-feedback">`)}
            ${field('smtp-enc', 'SMTP 加密', `<select id="f-smtp-enc" class="select">${options(v.smtpEnc)}</select>`)}
            ${field('imap-port', 'IMAP 端口', `<input id="f-imap-port" class="input" inputmode="numeric" value="${esc(v.imapPort)}" aria-describedby="imap-port-feedback">`)}
            ${field('imap-enc', 'IMAP 加密', `<select id="f-imap-enc" class="select">${options(v.imapEnc)}</select>`)}
            <div class="field field-wide">
              <label class="field-label" for="f-smtp-user">SMTP 用户名</label>
              <input id="f-smtp-user" class="input" autocomplete="off" spellcheck="false" placeholder="默认与邮箱地址相同" value="${esc(v.smtpUser)}">
            </div>
          </div>
        </div>
      </div>
      ${testNotice()}`;
  }

  function openModal(brand, init = {}) {
    const b = BRANDS[brand];
    if (!b || b.kind === 'reserved') return;
    const mb = state.mailbox;
    const reconnect = init.mode === 'reconnect' && !!mb && mb.brand === brand;
    const m = {
      brand,
      mode: reconnect ? 'reconnect' : 'connect',
      values: { account: '', code: '', address: '', smtp: '', imap: '', smtpPort: '465', smtpEnc: 'ssl', imapPort: '993', imapEnc: 'ssl', smtpUser: '' },
      errors: {}, helpOpen: false, advOpen: false, phase: 'edit', frozen: false, token: 0,
      el: {},
      lastTrigger: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    };
    if (reconnect) {
      if (b.kind === 'custom') Object.assign(m.values, mb, { code: '' });
      else if (b.kind === 'suffix') m.values.account = mb.address.slice(0, -(`@${b.domain}`).length);
      else m.values.address = mb.address;
    }
    Object.assign(m, init);
    ['account', 'code', 'address', 'smtp', 'imap'].forEach((key) => { if (init[key] !== undefined) m.values[key] = init[key]; });

    state.modal = m;
    $('#connect-title-icon').innerHTML = brandIcon(brand, 32);
    $('#connect-title-text').textContent = `连接${/^[A-Za-z0-9]/.test(b.name) ? ' ' : ''}${b.name}`;
    modal.classList.toggle('is-lg', b.kind === 'custom');
    body.innerHTML = b.kind === 'custom' ? customBody(m) : (b.kind === 'suffix' ? suffixBody(m, b) : addressBody(m, b));
    body.scrollTop = 0;
    $$('input, select', body).forEach((input) => {
      const key = input.id.slice(2);
      m.el[key] = input;
      input.addEventListener('input', () => { delete m.errors[key]; syncModal(); });
    });
    $('#toggle-help', body)?.addEventListener('click', () => { m.helpOpen = !m.helpOpen; syncModal(); });
    $('#toggle-adv', body)?.addEventListener('click', () => { m.advOpen = !m.advOpen; syncModal(); });

    // 场景预设：直接呈现校验未通过的状态
    if (init.showErrors) m.errors = validate(m);
    syncModal();
    overlay.hidden = false;
    const first = b.kind === 'suffix' ? m.el.account : m.el.address;
    if (reconnect || (first && first.value)) m.el.code.focus(); else first?.focus();
  }

  function closeModal() {
    const m = state.modal;
    if (!m) return;
    state.modal = null;
    m.token++;
    overlay.hidden = true;
    if (m.lastTrigger && m.lastTrigger.isConnected) m.lastTrigger.focus();
  }

  function currentAddress(m) {
    const b = BRANDS[m.brand];
    const raw = (b.kind === 'suffix' ? m.el.account : m.el.address).value.trim();
    if (b.kind !== 'suffix' || raw.includes('@')) return raw;
    return raw ? `${raw}@${b.domain}` : '';
  }

  function validate(m) {
    const errors = {};
    const b = BRANDS[m.brand];
    const val = (key) => (m.el[key] ? m.el[key].value.trim() : '');
    if (b.kind === 'suffix') {
      if (!val('account')) errors.account = '请输入邮箱账号';
      else if (val('account').includes('@') && !val('account').toLowerCase().endsWith(`@${b.domain}`)) {
        errors.account = `这里只能连接${/^[A-Za-z0-9]/.test(b.name) ? ' ' : ''}${b.name}，其他邮箱请选择对应的品牌或「自定义邮箱」`;
      }
      if (!val('code')) errors.code = '请输入授权码';
      return errors;
    }
    if (!val('address')) errors.address = '请输入邮箱地址';
    else if (!EMAIL_RE.test(val('address'))) errors.address = '请输入完整的邮箱地址，如 name@company.com';
    if (!val('code')) errors.code = b.codeRequiredText || '请输入密码或授权码';
    if (b.kind === 'address') return errors;

    const port = (key) => {
      if (!/^\d{1,5}$/.test(val(key)) || Number(val(key)) < 1 || Number(val(key)) > 65535) errors[key] = '请输入 1–65535 的端口号';
    };
    if (!val('smtp')) errors.smtp = '请输入发件服务器地址';
    if (!val('imap')) errors.imap = '请输入收件服务器地址';
    if (m.advOpen) { port('smtp-port'); port('imap-port'); }
    return errors;
  }

  function setFeedback(m, key, text, isError) {
    const node = $(`#${key}-feedback`, body);
    const input = m.el[key];
    if (node) { node.hidden = !text; node.classList.toggle('is-error', !!isError); node.innerHTML = text || ''; }
    if (input) { input.classList.toggle('is-error', !!isError); input.setAttribute('aria-invalid', String(!!isError)); }
  }

  function syncModal() {
    const m = state.modal;
    if (!m) return;
    const b = BRANDS[m.brand];
    const busy = m.phase === 'connecting';
    const address = currentAddress(m);
    const replace = $('#replace-notice', body);

    if (replace) {
      const show = !!state.mailbox && address.toLowerCase() !== state.mailbox.address.toLowerCase();
      replace.hidden = !show;
      if (show) $('#replace-address', replace).textContent = state.mailbox.address;
    }
    if (b.kind === 'suffix') {
      m.el.account.nextElementSibling.hidden = m.el.account.value.includes('@');
      const error = m.errors.account;
      setFeedback(m, 'account', error || (address ? `将连接 <b>${esc(address)}</b>` : ''), !!error);
    } else {
      ['address', 'smtp', 'imap', 'smtp-port', 'imap-port'].forEach((key) => setFeedback(m, key, m.errors[key] || '', !!m.errors[key]));
    }
    setFeedback(m, 'code', m.errors.code || '', !!m.errors.code);

    const helpToggle = $('#toggle-help', body);
    if (helpToggle) { helpToggle.setAttribute('aria-expanded', String(m.helpOpen)); $('#help-steps', body).hidden = !m.helpOpen; }
    const advToggle = $('#toggle-adv', body);
    if (advToggle) { advToggle.setAttribute('aria-expanded', String(m.advOpen)); $('#adv-body', body).hidden = !m.advOpen; }

    $$('input, select, button', body).forEach((el) => { el.disabled = busy; });
    $$('a', body).forEach((a) => { a.setAttribute('aria-disabled', String(busy)); a.tabIndex = busy ? -1 : 0; });
    $('#connect-cancel').disabled = busy;
    $('#connect-close').disabled = busy;
    $('#connect-submit').disabled = busy;
    $('#connect-spinner').hidden = !busy;
    $('#connect-submit-text').textContent = busy ? '连接中…' : (m.phase === 'failed' ? '重试' : '连接');

    const errorBox = $('#connect-error', body);
    if (m.phase === 'failed') {
      const f = FAILURE[m.brand];
      errorBox.hidden = false;
      errorBox.innerHTML = `${icon('alert')}<div class="notice-body"><p class="notice-title">连接失败：${f.title}</p><p class="notice-text">${f.text}</p>${b.kind !== 'custom' ? `<button type="button" class="btn-link" id="error-help">${f.link || '查看获取步骤'}</button>` : ''}</div>`;
      // 展开步骤后停在顶部：失败原因和处理办法继续可见，步骤在下方往下看
      $('#error-help', errorBox)?.addEventListener('click', () => { m.helpOpen = true; syncModal(); body.scrollTop = 0; });
    } else {
      errorBox.hidden = true;
    }
    // 刚进入失败状态时回到顶部，保证失败提示在可视区内
    if (m.phase === 'failed' && m.lastPhase !== 'failed') body.scrollTop = 0;
    m.lastPhase = m.phase;
  }

  function finishConnect(m) {
    const b = BRANDS[m.brand];
    const address = currentAddress(m);
    const val = (key) => (m.el[key] ? m.el[key].value.trim() : '');
    state.mailbox = { brand: m.brand, address, since: today() };
    if (b.kind === 'custom') {
      Object.assign(state.mailbox, {
        smtp: val('smtp'), imap: val('imap'),
        smtpPort: val('smtp-port'), smtpEnc: m.el['smtp-enc'].value,
        imapPort: val('imap-port'), imapEnc: m.el['imap-enc'].value,
        smtpUser: val('smtp-user'),
      });
    }
    state.modal = null;
    m.token++;
    overlay.hidden = true;
    renderPage();
    $('#btn-reconnect')?.focus();
    showToast(`邮箱连接成功，测试邮件已发送至 ${address}`);
  }

  $('#connect-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const m = state.modal;
    if (!m || m.phase === 'connecting') return;
    m.errors = validate(m);
    if (Object.keys(m.errors).length) {
      m.phase = 'edit';
      syncModal();
      m.el[Object.keys(m.errors)[0]]?.focus();
      return;
    }
    m.phase = 'connecting';
    syncModal();
    const token = ++m.token;
    // 原型约定：密码或授权码填 wrong 时模拟连接失败，方便查看各品牌的失败提示
    if (!m.frozen) setTimeout(() => {
      if (state.modal !== m || token !== m.token) return;
      if (m.el.code.value.trim().toLowerCase() === 'wrong') { m.phase = 'failed'; syncModal(); } else finishConnect(m);
    }, 1500);
  });
  $('#connect-cancel').addEventListener('click', closeModal);
  $('#connect-close').addEventListener('click', closeModal);

  function trapFocus(e) {
    const focusable = $$('button, a[href], input, select', modal).filter((el) => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length > 0);
    if (!focusable.length) { e.preventDefault(); modal.focus(); return; }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !modal.contains(active))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (active === last || !modal.contains(active))) { e.preventDefault(); first.focus(); }
  }

  /* ---------- 侧栏与连接中心菜单 ---------- */
  function setFlyout(open) {
    const trigger = $('#connection-trigger');
    const canOpen = open && trigger.getClientRects().length > 0;
    state.flyout = canOpen;
    flyout.hidden = !canOpen;
    trigger.setAttribute('aria-expanded', String(canOpen));
    if (canOpen) placeFlyout();
  }

  function placeFlyout() {
    if (flyout.hidden) return;
    const base = frame.getBoundingClientRect();
    const side = $('#sidebar').getBoundingClientRect();
    const trigger = $('#connection-trigger').getBoundingClientRect();
    const x = Math.max(8, Math.min(side.right - base.left - 8, frame.clientWidth - flyout.offsetWidth - 8));
    const y = Math.max(8, Math.min(trigger.top - base.top - 6, frame.clientHeight - flyout.offsetHeight - 8));
    flyout.style.left = `${x}px`;
    flyout.style.top = `${y}px`;
  }

  const closeDrawer = () => { app.classList.remove('is-drawer-open'); $('#sidebar-backdrop').hidden = true; };
  const openDrawer = () => { app.classList.add('is-drawer-open'); $('#sidebar-backdrop').hidden = false; };

  $('#connection-trigger').addEventListener('click', () => setFlyout(!state.flyout));
  $('#connection-trigger').addEventListener('mouseenter', () => setFlyout(true));
  flyout.addEventListener('click', (e) => { if (e.target.closest('.flyout-item')) setFlyout(false); });
  // 点击页面其他位置收起菜单；原型导航条和设计说明面板里的点击不收起（它们可能刚把菜单打开）
  document.addEventListener('click', (e) => {
    if (state.flyout && !e.target.closest('#flyout-connection, #connection-trigger, .proto-bar, .notes')) setFlyout(false);
  });
  $('#btn-collapse').addEventListener('click', () => { setFlyout(false); app.classList.add('is-collapsed'); });
  $('#btn-sidebar').addEventListener('click', () => {
    if (app.classList.contains('is-narrow')) app.classList.contains('is-drawer-open') ? closeDrawer() : openDrawer();
    else app.classList.remove('is-collapsed');
  });
  $('#sidebar-backdrop').addEventListener('click', closeDrawer);

  document.addEventListener('keydown', (e) => {
    if (state.modal) {
      if (e.key === 'Escape') { if (state.modal.phase !== 'connecting') { e.preventDefault(); closeModal(); } }
      else if (e.key === 'Tab') trapFocus(e);
      return;
    }
    if (e.key !== 'Escape') return;
    if (app.classList.contains('is-drawer-open')) closeDrawer(); else setFlyout(false);
  });

  function updateLayout() {
    const narrow = frame.clientWidth < 768;
    app.classList.toggle('is-narrow', narrow);
    if (!narrow) closeDrawer();
    placeFlyout();
  }

  /* ---------- 提示与设计说明 ---------- */
  let toastTimer = 0;
  function showToast(text) {
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 3000);
  }

  function renderNotes() {
    const list = $('#notes');
    list.innerHTML = `<h2>设计说明</h2><p>编号对应界面上的标记，点击可跳到对应场景。</p>${NOTES.map((note) => `
      <${note.scene ? 'button type="button"' : 'div'} class="note-item${note.scene ? '' : ' is-static'}" ${note.scene ? `data-note-scene="${note.scene}"` : ''}>
        <span class="note-no">${note.n}</span><span><strong>${note.title}</strong><span>${note.text}</span></span>
      </${note.scene ? 'button' : 'div'}>`).join('')}`;
  }

  $('#notes').addEventListener('click', (e) => {
    const item = e.target.closest('[data-note-scene]');
    if (item) applyScene(item.dataset.noteScene);
  });
  $('#btn-notes').addEventListener('click', () => {
    const on = !document.body.classList.contains('show-notes');
    document.body.classList.toggle('show-notes', on);
    $('#notes').hidden = !on;
    $('#btn-notes').setAttribute('aria-pressed', String(on));
  });

  /* ---------- 场景与界面宽度 ---------- */
  const QQ = { brand: 'qq', address: '987654321@qq.com', since: '2026-11-24' };
  const EXMAIL = { brand: 'exmail', address: 'zhang.wei@minghua.com', since: '2026-11-24' };
  const COMPANY = 'zhang.wei@minghua.com';
  const SCENES = {
    entry: { hint: '连接中心子菜单新增「连接邮箱」。把鼠标移到左侧「连接中心」上即可看到，点击它进入页面。', apply() { setFlyout(true); } },
    empty: { hint: '未连接：分「个人邮箱」「企业邮箱」两组。虚线卡片是预留位置，点击其余卡片开始连接。', apply() {} },
    connected: { hint: '已连接：显示邮箱、状态和管理操作。下方「更换邮箱」可改连其他邮箱。', apply() { state.mailbox = { ...EXMAIL }; } },
    replace: { hint: '已有连接时选择其他品牌：弹窗顶部提示会替换当前邮箱。', apply() { state.mailbox = { ...QQ }; openModal('163'); } },
    reconnect: { hint: '点卡片上的「重新连接」：地址已带出，只需重新输入密码或授权码。', apply() { state.mailbox = { ...EXMAIL }; openModal('exmail', { mode: 'reconnect' }); } },
    disconnect: { hint: '点「断开连接」会先弹出确认（浏览器确认框，与现有页面一致）。', apply() { state.mailbox = { ...QQ }; setTimeout(askDisconnect, 80); } },
    error: { hint: '加载失败：说明原因并提供重试。', apply() { state.view = 'error'; } },

    qq: { hint: 'QQ邮箱：只需填 QQ 号和授权码。填写后点「连接」可走完整流程。', apply() { openModal('qq'); } },
    'qq-help': { hint: '展开「如何获取授权码」后的步骤说明和官方链接。', apply() { openModal('qq', { account: '987654321', helpOpen: true }); } },
    '163': { hint: '163邮箱：只需填账号名和授权码。', apply() { openModal('163'); } },
    invalid: { hint: '未填内容直接点「连接」：字段下方给出提示，焦点落在第一个未填项。', apply() { openModal('qq', { showErrors: true }); } },
    connecting: { hint: '连接中：按钮转圈并禁用，关闭和取消不可用（实际约需几秒）。', apply() { openModal('qq', { account: '987654321', code: '****************', phase: 'connecting', frozen: true }); } },
    failed: { hint: '连接失败：在弹窗内说明原因和下一步，可以直接修改后重试。在任一品牌弹窗里把密码或授权码填成 wrong，可以模拟该品牌的失败提示。', apply() { openModal('qq', { account: '987654321', code: '****************', phase: 'failed' }); } },

    exmail: { hint: '腾讯企业邮箱：地址用公司自己的域名，填完整地址和密码（或客户端专用密码）。服务器由系统自动尝试，不用选。', apply() { openModal('exmail'); } },
    'exmail-help': { hint: '展开「怎么填写和开通」：开启服务、生成专用密码的步骤，以及需要公司邮箱管理员开通的情况。', apply() { openModal('exmail', { address: COMPANY, helpOpen: true }); } },
    'exmail-failed': { hint: '腾讯企业邮箱：通用、信创两套服务器都试过仍失败时的提示。', apply() { openModal('exmail', { address: COMPANY, code: '****************', phase: 'failed' }); } },
    qiye: { hint: '网易企业邮箱：付费版（华北、华东）和免费版并成一个入口，只填地址和授权码（或密码）。服务器由系统自动尝试，不用选。', apply() { openModal('qiye'); } },
    'qiye-help': { hint: '展开「怎么填写和开通」：付费版生成授权码的步骤、免费版直接用密码，以及需要公司邮箱管理员开通的情况。', apply() { openModal('qiye', { address: COMPANY, helpOpen: true }); } },
    'qiye-failed': { hint: '网易企业邮箱：付费版华北、付费版华东、免费版三套服务器都试过仍失败时的提示。', apply() { openModal('qiye', { address: COMPANY, code: '****************', phase: 'failed' }); } },

    custom: { hint: '自定义邮箱：默认只需填 4 项，服务器填写邮箱提供商给出的地址。', apply() { openModal('custom'); } },
    'custom-advanced': { hint: '展开「高级设置」：端口、加密方式、SMTP 用户名。', apply() { openModal('custom', { address: 'li.wei@contoso.com', smtp: 'smtp.contoso.com', imap: 'imap.contoso.com', advOpen: true }); } },
  };

  function applyScene(name) {
    const scene = SCENES[name];
    const m = state.modal;
    if (m) m.token++;
    state.modal = null;
    overlay.hidden = true;
    state.mailbox = null;
    state.view = 'ready';
    app.classList.remove('is-collapsed');
    closeDrawer();
    setFlyout(false);

    scene.apply();
    renderPage();
    syncModal();
    updateLayout();

    let hint = scene.hint;
    if (name === 'entry' && !state.flyout) hint = NOTES[13].text;
    $('#scene-hint').textContent = hint;
    $('#scene-hint').hidden = false;
    $$('.chip[data-scene]').forEach((chip) => chip.classList.toggle('is-active', chip.dataset.scene === name));
    state.scene = name;
  }

  function setWidth(width) {
    frame.classList.toggle('is-auto', width === 'auto');
    frame.style.width = width === 'auto' ? '' : `${width}px`;
    $$('.chip[data-width]').forEach((chip) => chip.classList.toggle('is-active', chip.dataset.width === width));
  }

  $$('.chip[data-scene]').forEach((chip) => chip.addEventListener('click', () => applyScene(chip.dataset.scene)));
  $$('.chip[data-width]').forEach((chip) => chip.addEventListener('click', () => setWidth(chip.dataset.width)));

  /* ---------- 初始化 ---------- */
  $$('[data-icon]').forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
  new ResizeObserver(updateLayout).observe(frame);
  renderNotes();
  setWidth('auto');
  applyScene('empty');
})();
