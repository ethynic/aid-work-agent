# 阿里云 ASR AccessKey 更换指南

> 适用场景：ASR 相关凭证（`ALIYUN_ASR_ACCESS_KEY_ID` / `ALIYUN_ASR_ACCESS_KEY_SECRET`）到期或泄露时重新申请。2026-09-16 因到期更换，流程记录备查。

## 三个凭证的关系

| 环境变量 | 来源 | 作用 |
|---------|------|------|
| `ALIYUN_ASR_APPKEY` | 智能语音交互控制台「项目管理」 | 标识 ASR 项目 |
| `ALIYUN_ASR_ACCESS_KEY_ID` | RAM 访问控制台 | 阿里云 API 身份凭证 |
| `ALIYUN_ASR_ACCESS_KEY_SECRET` | 与 ID 同次创建 | 配对密钥，用于换取调用 Token |

调用链：AK ID + AK Secret -> 换 Token -> Token + AppKey 调用 ASR 接口。

## 获取步骤

1. **登录阿里云控制台，进入 RAM 访问控制**（`ram.console.aliyun.com`，顶部搜索「RAM 访问控制」）。
2. **创建 RAM 用户**（已有可跳过）：左侧「身份管理 -> 用户」->「创建用户」，勾选 **「Open API 调用访问」**，勾选后自动进入 AccessKey 创建流程。
3. **创建 AccessKey**：用户详情页 ->「认证管理」->「AccessKey 密钥」->「创建 AccessKey」。
   - ⚠️ **AccessKey Secret 只显示这一次**，立即复制保存；关闭弹窗后无法再查看，丢失只能重新创建。
4. **授权（必须，否则获取 Token 报 403）**：用户详情页 ->「权限管理」->「新增授权」，授予系统权限策略 `AliyunNLSFullAccess`（只读场景可用 `AliyunNLSReadOnlyAccess`）。
5. **AppKey**：智能语音交互控制台（`nls-portal.console.aliyun.com`）->「项目管理」查看，无需重新申请。

## 更换后操作

1. 将新 AK ID / AK Secret 更新到 `.env` 及服务器环境变量（重启后端生效）。
2. 验证语音识别功能正常（如渠道语音消息转写）。
3. 建议使用 RAM 子账号而非主账号 AK，遵循最小权限原则。

## 常见错误：FREE_TRIAL_EXPIRED（40000010）

```
status=40000010, message="Gateway:FREE_TRIAL_EXPIRED:The free trial has expired!"
```

- **不是 key 的问题**：请求已通过 AK 鉴权（认证失败报 InvalidAccessKeyId / 401），卡在服务层
- **根因**：账号开通的是「智能语音交互·免费试用版」，试用过期。阿里云将试用版和正式版分为两个独立 SKU，试用过期后换任何新 key 都报此错
- **解决**：智能语音交互控制台（`nls-portal.console.aliyun.com`）->「开通正式版 / 升级为商用版」-> 选按量付费开通。开通后原 AppKey 一般可直接用；仅当提示试用版项目不可迁移时，才在正式版下重建项目并更换 `ALIYUN_ASR_APPKEY`

## 2026-09-16 实际排查记录（agent2 测试环境）

现象：开通一句话识别商用版 + 新建项目 + 改 `.env` + `docker restart` 后，仍报 FREE_TRIAL_EXPIRED。

**真正根因（两层叠加）**：

1. **`docker restart` 不重新读取 `.env`**：环境变量在容器**创建**时注入，restart 只重启进程。当时容器创建于 14:47（env 已固化为旧值），`.env` 15:35 才改，restart 后容器内仍是旧 AppKey。**必须用 `docker compose -f docker-compose.test.yml up -d aid-agent-api` 重建容器**（compose 服务名 `aid-agent-api`，容器名 `aid-agent-api2`，注意区分）
2. **旧 AppKey 属于试用版时期的项目**：容器重建前实际用的是旧 AppKey，走试用网关必然报错；与新 key 无关

**验证方法**（容器内直接打网关，绕开业务代码）：

```bash
# 无效音频：预期 40270004 NO_VALID_AUDIO_ERROR（商用版生效的判别信号）
docker exec aid-agent-api2 python -c "
import asyncio, aiohttp, os
from src.tools.asr.speech_to_text_tool import SpeechToTextTool

async def main():
    t = SpeechToTextTool()
    token = await t._get_or_refresh_token(os.environ['ALIYUN_ASR_ACCESS_KEY_ID'], os.environ['ALIYUN_ASR_ACCESS_KEY_SECRET'])
    url = 'https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr?appkey=' + os.environ['ALIYUN_ASR_APPKEY'] + '&format=wav&sample_rate=16000'
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers={'X-NLS-Token': token}, data=b'x') as r:
            print(r.status, await r.text())
asyncio.run(main())
"
```

结果判读：仍报 FREE_TRIAL_EXPIRED -> 网关侧未生效（等延迟/查项目归属）；报格式类错误（如 40270004）-> 商用版已生效；`docker exec env | grep ALIYUN_ASR` 可确认 env 是否真的进入容器。
