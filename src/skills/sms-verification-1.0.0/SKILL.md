---
name: sms-verification
description: >
  发送/验证手机短信验证码，用于身份核实、二次确认、敏感操作授权等场景。
  仅支持验证码场景，复用系统内置短信通道与 sms_codes 表，含 60s 防秒刷与 24h 配额频控。
  触发关键词：发验证码 / 短信验证 / 手机验证 / 身份核实 / 验证码校验。
metadata:
  openclaw:
    emoji: "📱"
    requires:
      bins: ["python"]
---

# 短信验证码技能

## 何时使用此技能

**使用短信验证码用于**：
- 用户身份核实（如登录二次校验、找回账号）
- 敏感操作授权（如修改密码、变更重要资料）
- 关键业务确认（如订单确认、支付确认）
- 任何需要"证明该手机号属于本人"的场景

**关键词触发**：
- 发验证码 / 短信验证 / 手机验证
- 身份核实 / 实名验证
- 验证码校验 / 校验验证码
- 短信确认 / 手机确认

---

## ⛔ 强制限制 - 必须遵守 ⛔

1. **仅使用 `python scripts/sms_cli.py`** - 禁止建议用户通过其他方式发送短信验证码（如直接调用第三方 API、自行实现发送逻辑等）
2. **不要尝试其他方法** - 本技能仅复用系统内置短信通道，不提供自定义模板、不提供其他通道
3. **如果脚本失败** - 直接将错误信息返回给用户并停止，**不要提供回退方案**
4. **禁止回显验证码** - 脚本输出 JSON 不含 `code` 字段；对话中也不得向用户复述验证码值
5. **禁止透传日志中的验证码** - 脚本日志中验证码一律脱敏为 `***`，不得将日志原文转述给用户

如果脚本执行失败（通道未配置、频控拦截、网络错误等）：
- 向用户显示错误消息（脚本输出的 `error` 字段）
- 不要提供替代方案（如"换用邮箱验证"、"联系客服手动发送"等）
- 等待用户解决配置或等待频控窗口过后重试

---

## 如何使用此技能

### 1. 发送验证码

**必需参数**：
- `--mobile`：接收验证码的手机号（11 位数字，1 开头）

**可选参数**（仅用于日志追踪，不影响业务逻辑）：
- `--tenant-id`：租户 ID
- `--user-id`：用户 ID
- `--session-id`：会话 ID

**示例**：
```bash
python scripts/sms_cli.py send --mobile 13800138000
```

**返回结果**：
```json
{"success": true, "expires_in_seconds": 900}
```

**失败场景**：
- 手机号格式错误：`{"success": false, "error": "手机号格式错误，必须是 11 位数字", ...}`
- 通道未配置：`{"success": false, "error": "短信通道未配置，请联系管理员", ...}`
- 60s 频控：`{"success": false, "error": "发送过于频繁，请 N 秒后重试", "reason": "interval_too_short", ...}`
- 24h 上限：`{"success": false, "error": "24 小时内发送次数已达上限（10 次），请明日再试", "reason": "daily_limit_exceeded"}`
- 发送失败：`{"success": false, "error": "验证码发送失败，请稍后重试", ...}`

### 2. 校验验证码

**必需参数**：
- `--mobile`：手机号
- `--code`：用户输入的验证码（6 位数字）

**可选参数**：同 `send`

**示例**：
```bash
python scripts/sms_cli.py verify --mobile 13800138000 --code 123456
```

**返回结果**：
```json
{"success": true, "reason": "ok"}
```

**reason 取值**：
| reason | 含义 |
|--------|------|
| `ok` | 验证码正确且未过期 |
| `invalid` | 验证码错误、已过期、已使用过，或手机号格式错误 |
| `bypass` | 命中 `qb_sms_code` 配置的 bypass 码（仅测试/演示用） |

**失败结果**：
```json
{"success": false, "reason": "invalid", "error": "验证码错误或已过期"}
```

---

## 频控规则

| 维度 | 限制 | 说明 |
|------|------|------|
| 60 秒间隔 | 同一手机号 60s 内只能发送 1 次 | 防秒刷 |
| 24 小时配额 | 同一手机号 24h 内最多 10 次 | 防配额消耗 |

频控通过 Redis 实现，Redis 不可用时降级到内存（仅单 worker 有效），并记录 warning 日志。

---

## 安全条款

1. **验证码不得回显**：脚本输出 JSON 严格不含 `code` 字段；AI 代理在对话中也禁止向用户复述验证码值
2. **日志脱敏**：脚本日志中验证码一律以 `***` 表示，错误信息中的敏感字段（password、token、api_key、code=数字）自动脱敏
3. **无回退方案**：发送/校验失败时直接报错，不提供"换通道"、"联系客服手动发送"等替代方案
4. **bypass 码仅限测试**：`qb_sms_code` 配置项仅用于测试/演示环境，生产环境应留空

---

## 与底层 sms_codes 表的关系

本技能**不新建数据库表**，复用 `src/db/models.py` 中的：
- `send_sms_code(phone)` - 生成验证码、调用短信通道、写入 `sms_codes` 表（15 分钟 TTL，演示模式固定 `888888`）
- `verify_sms_code(phone, code)` - 校验 `sms_codes` 表，命中后标记 `used=1`

因此本技能无需 `init_script`，不创建 `init_tables.py`。

---

## 配置项

本技能依赖以下配置（通过 `configs/config.yaml` 或环境变量）：

| 配置项 | 环境变量 | 说明 |
|--------|---------|------|
| `sms.channel` | `SMS_CHANNEL` | 短信通道，如 `ZhuTong` |
| `sms.username` | `SMS_USERNAME` | 通道账号 |
| `sms.password` | `SMS_PASSWORD` | 通道密码 |
| `sms.signature` | `SMS_SIGNATURE` | 短信签名（不带【】） |
| `sms.template_yzm` | `SMS_TEMPLATE_YZM` | 验证码模板 ID |
| `sms.qb_sms_code` | `QBSMSCODE` | bypass 码（测试/演示用，生产留空） |
| `demo.enabled` | `DEMO_ENABLED` | 演示模式开关，开启后固定 `888888` 不实际发送 |

通道未配置时，`send` 子命令会直接返回失败；`verify` 子命令不依赖通道配置。

---

## 测试技能

验证脚本可执行性（不会实际发送）：
```bash
python scripts/sms_cli.py send --mobile 13800138000
python scripts/sms_cli.py verify --mobile 13800138000 --code 888888
```

单元测试见 `tests/unit/tools/test_sms_skill_cli.py`。
