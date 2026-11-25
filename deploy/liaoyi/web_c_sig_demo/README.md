# web_c 签名链接（ext 模式）demo 官网

模拟租户官网自有登录体系，用于端到端测试 web_c 渠道签名链接模式。部署在聊易私有云（agent.0635easy.com）。

## 文件

| 文件 | 用途 | 服务器位置 |
|------|------|-----------|
| `web_c_sig_demo.html` | 极简登录页（表单 + fetch 调后端 + 跳转签名链接） | `/var/www/agent/www_static/` |
| `web_c_sig_demo_server.py` | 登录后端：校验硬编码账号 -> HMAC 签名 -> 返回聊天页 URL（纯 stdlib，零依赖） | `/var/www/agent/www_static/` |
| `web-c-sig-demo.service` | systemd 常驻单元 | `/etc/systemd/system/` |
| `patch_nginx.py` | nginx conf 定向插入 `location /web_c_sig_demo/api/`（幂等，可重跑） | 临时用，部署后删除 |

⚠️ 含 `channel_secret` 与渠道 ID，仅 demo 用途。

## 协议

- `sig = HMAC-SHA256(key=channel_secret, msg=f"{u}.{ts}.{name}")` hex 小写；ts = unix 秒，服务端时效 24h（`src/saas/api/web_c.py` `_verify_ext_signature`）
- 账号映射（userid 须匹配 `^[A-Za-z0-9_\-@.]{1,128}$`，故中文用户名映射拼音 userid）：
  - 张三 / 123 -> `u=zhangsan`、`name=张三`
  - 李四 / 123 -> `u=lisi`、`name=李四`
- 登录成功跳转：`https://agent.0635easy.com/c/tenant_569f4e5acf4d/chan_82e934e3da46?u=...&ts=...&name=...&sig=...`

## 部署步骤（聊易服务器）

```bash
# 1. 文件复制
scp web_c_sig_demo.html web_c_sig_demo_server.py web-c-sig-demo.service liaoyi:/var/www/agent/www_static/
ssh liaoyi chmod 644 /var/www/agent/www_static/web_c_sig_demo*

# 2. systemd 后端（监听 127.0.0.1:8790）
ssh liaoyi "sudo cp /var/www/agent/www_static/web-c-sig-demo.service /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now web-c-sig-demo"

# 3. nginx location（幂等补丁 + 语法检查 + reload）
scp patch_nginx.py liaoyi:/tmp/ && ssh liaoyi "sudo python3 /tmp/patch_nginx.py \
  && sudo nginx -t && sudo systemctl reload nginx"
```

## 验证

```bash
curl -s https://agent.0635easy.com/web_c_sig_demo/api/health
curl -s -X POST https://agent.0635easy.com/web_c_sig_demo/api/login \
  -H 'Content-Type: application/json' -d '{"username":"李四","password":"123"}'
# 用返回的签名链接实测 ext 验签（应返回 mode:ext 与 identity 回显）：
curl -s "https://agent.0635easy.com/api/c/web_c/tenant_569f4e5acf4d/chan_82e934e3da46/bootstrap?u=lisi&ts=...&name=...&sig=..."
```

2026-11-25 首次部署端到端验证已通过（bootstrap 返回 `mode: ext, channel_user_id: ext_lisi, display_name: 李四`）。

## 运维

- 后端日志：`journalctl -u web-c-sig-demo -f`（登录成功/失败事件带 IP 与用户名）
- 限速：单 IP 每 60s 最多 10 次登录尝试（内存态，超限 429）
- 更新后端代码：覆盖 `web_c_sig_demo_server.py` 后 `sudo systemctl restart web-c-sig-demo`
- 静态页更新：覆盖 html 即生效（nginx no-cache 直读）
