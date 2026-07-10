# sms-verification skill

发送 / 校验手机短信验证码，用于身份核实、二次确认、敏感操作授权等场景。

## 目录结构

```
sms-verification-1.0.0/
├── SKILL.md            # 触发说明 + 安全条款（供 AI 代理阅读）
├── _meta.json          # 技能元数据
├── README.md           # 本文件（开发者参考）
└── scripts/
    └── sms_cli.py      # CLI: send / verify 子命令
```

> 本技能复用现有 `sms_codes` 表与 `src/db/models.py` 中的 `send_sms_code` / `verify_sms_code`，
> 不新建数据库表，因此不声明 `init_script`，不创建 `init_tables.py`。

## 依赖

- Python >= 3.8
- 项目内置模块：`src.sms.manager`、`src.db.models`、`src.core.redis_client`、`src.config.settings`
- 无额外 pip 依赖

## 配置项

通过 `configs/config.yaml` 或环境变量配置：

| 配置项 | 环境变量 | 必填 | 说明 |
|--------|---------|------|------|
| `sms.channel` | `SMS_CHANNEL` | 是 | 短信通道，如 `ZhuTong` |
| `sms.username` | `SMS_USERNAME` | 是 | 通道账号 |
| `sms.password` | `SMS_PASSWORD` | 是 | 通道密码 |
| `sms.signature` | `SMS_SIGNATURE` | 是 | 短信签名（不带【】） |
| `sms.template_yzm` | `SMS_TEMPLATE_YZM` | 是 | 验证码模板 ID |
| `sms.qb_sms_code` | `QBSMSCODE` | 否 | bypass 码（测试/演示用，生产留空） |
| `demo.enabled` | `DEMO_ENABLED` | 否 | 演示模式开关，开启后固定 `888888` 不实际发送 |

通道未配置时，`send` 子命令直接返回失败；`verify` 子命令不依赖通道配置。

## 频控规则

| 维度 | 限制 | 说明 |
|------|------|------|
| 60 秒间隔 | 同一手机号 60s 内只能发送 1 次 | 防秒刷 |
| 24 小时配额 | 同一手机号 24h 内最多 10 次 | 防配额消耗 |

频控通过 `src.core.redis_client.RedisClient` 实现：
- Redis 可用：跨 worker 一致
- Redis 不可用：降级到内存（仅单 worker 有效），并记录 warning 日志

## 用法示例

### 发送验证码

```bash
python scripts/sms_cli.py send --mobile 13800138000
```

成功响应（**不含验证码**）：
```json
{"success": true, "expires_in_seconds": 900}
```

失败响应：
```json
{"success": false, "error": "发送过于频繁，请 45 秒后重试", "reason": "interval_too_short", "retry_after_seconds": 45}
```

### 校验验证码

```bash
python scripts/sms_cli.py verify --mobile 13800138000 --code 123456
```

成功响应：
```json
{"success": true, "reason": "ok"}
```

失败响应：
```json
{"success": false, "reason": "invalid", "error": "验证码错误或已过期"}
```

`reason` 取值：
| reason | 含义 |
|--------|------|
| `ok` | 验证码正确且未过期 |
| `invalid` | 验证码错误、已过期、已使用过，或手机号格式错误 |
| `bypass` | 命中 `qb_sms_code` 配置的 bypass 码（仅测试/演示用） |

## 安全条款

1. **验证码不得回显**：脚本输出 JSON 严格不含 `code` 字段；对话中也不得向用户复述验证码值
2. **日志脱敏**：脚本日志中验证码一律以 `***` 表示；错误信息中的敏感字段（password、token、api_key、code=数字）自动脱敏
3. **无回退方案**：发送/校验失败时直接报错，不提供"换通道"、"联系客服手动发送"等替代方案
4. **bypass 码仅限测试**：`qb_sms_code` 配置项仅用于测试/演示环境，生产环境应留空

## 与底层 sms_codes 表的关系

本技能复用 `src/db/models.py` 中的：

- `send_sms_code(phone)`：生成验证码 → 调用短信通道 → 写入 `sms_codes` 表（15 分钟 TTL，演示模式固定 `888888`）
- `verify_sms_code(phone, code)`：校验 `sms_codes` 表，命中后标记 `used=1`

因此本技能：
- 不新建数据库表
- 不声明 `init_script`
- 不创建 `init_tables.py`

## 测试

```bash
./scripts/dev_test.sh tests/unit/tools/test_sms_skill_cli.py -p no:cacheprovider -q
```
