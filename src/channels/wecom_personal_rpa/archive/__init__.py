"""企业微信个人账号 RPA — 服务端拉取会话存档模块

第一期 MVP：仅启用 listen_mode='server'。
- 回调接收 + 拉取密文 + RSA 解密 + 复用 _process_inbound_message
- 客户端模式（listen_mode='client'）后端代码保留，前端禁用，未来开放

详见 docs/system/wecom-personal-rpa-server-archive-listener-design.md
"""
