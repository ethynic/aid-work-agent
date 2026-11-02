# agent2 开放 Web 任务通知（Edge/Safari/Firefox）部署说明

> 功能设计：[Web 任务完成浏览器系统通知](../docs/system/web-task-push-notification-design.md)；
> 开发计划：[统一通知基础设施与 Web 推送开发计划](../docs/plans/plan-web-task-push-notification.md)。
> 本文档只覆盖 agent2（测试环境）的启用操作。执行前需用户明确指示（真机部署授权）。

## 现状核查（2026-11-02 实测）

| 项目 | 状态 |
|---|---|
| 容器代码 `src/notifications/` 全套 + dispatcher | ✅ 已部署（/var/www/agent2 挂载） |
| 容器依赖 pywebpush/py_vapid | ✅ 已安装（`import` 通过，无需重建镜像） |
| 数据库四张 notification_* 表 | ✅ 已建（aid_work_agent2） |
| nginx `/task-notifications-sw.js` | ✅ 200, application/javascript |
| 前端通知设置页入口 | ✅ 租户模式 → 头像菜单 → 通知设置 |
| `config.yaml` web_push / `.env` WEB_PUSH_* | ❌ enabled=false，密钥未配置 —— **本次要做的事** |
| 服务器 → 推送服务商连通性 | Edge(db3/db5.notify.windows.com) ✅ Firefox ✅ Apple ✅；**Chrome FCM 被墙 ❌** |

## 前置条件（已满足）

- HTTPS 域名 agent2.aidingyi.cn（有效证书，推送与 SW 注册必需）。
- 用户浏览器版本：Edge/Chrome 任意近版（Chrome 仅代理环境可用）；Firefox 44+；Safari 需 macOS 13+ / Safari 16.1+（iOS/iPadOS 需添加到主屏幕，首期不承诺）。
- 一个接收投递回执的联系邮箱（VAPID subject 用，格式 `mailto:xxx`）。

## 步骤 1：生成 VAPID 密钥（在 agent2 上执行）

利用 api2 容器内已装的 py_vapid 生成；私钥写文件（适配器支持路径加载，避免 PEM 换行进 .env 的问题），终端只输出公钥（公钥非机密，会下发给浏览器）。

```bash
cd /var/www/agent2
PUB=$(docker exec aid-agent-api2 python -c "
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
import base64, os
v = Vapid(); v.generate_keys()
pem = v.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
open('/app/configs/vapid_private_key.pem','wb').write(pem)
os.chmod('/app/configs/vapid_private_key.pem', 0o600)
pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(base64.urlsafe_b64encode(pub).decode().rstrip('='))
")
echo "PUBLIC_KEY=$PUB"   # 只含公钥，可安全回显
```

私钥落盘位置：宿主机 `/var/www/agent2/configs/vapid_private_key.pem`（容器内路径 `/app/configs/vapid_private_key.pem`，api/background 两容器均挂载 configs，已核实）。git reset 不删 untracked 文件，`*_update.sh` 更新不会误删。

## 步骤 2：写 .env（5 个变量）

已核实 `.env` 中不存在 WEB_PUSH 变量，直接追加即可（compose `env_file: .env` 自动注入）。

```bash
cd /var/www/agent2
cat >> .env <<EOF
WEB_PUSH_ENABLED=true
WEB_PUSH_VAPID_PUBLIC_KEY=$PUB
WEB_PUSH_VAPID_PRIVATE_KEY=/app/configs/vapid_private_key.pem
WEB_PUSH_VAPID_KEY_ID=v1
WEB_PUSH_VAPID_SUBJECT=mailto:换成实际联系邮箱
EOF
chmod 600 .env   # 原权限 777，含其他机密，顺手收紧
chmod 600 configs/vapid_private_key.pem 2>/dev/null || true
```

变量说明：`WEB_PUSH_ENABLED` 覆盖 config.yaml 的 `enabled: false`（settings.py 环境变量优先）；`KEY_ID` 固定 v1（轮换时才新增）；subject 必须是 `mailto:` 或 `https:`（适配器校验）。默认允许全部 active 租户（`allowed_tenants=null`），如需限租户再改 config.yaml，本次不动。

## 步骤 3：重建两个容器

只重建注入了新环境变量的 api 和 background（dispatcher 只跑在 background；runner-api/runner-worker 不需要动）。

```bash
cd /var/www/agent2
docker compose -f docker-compose.test.yml up -d --force-recreate aid-agent-api aid-agent-background
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'api2|background2'   # 期望 healthy
```

注意：`docker restart` 不会重读 env_file，必须 `up -d --force-recreate`。

## 步骤 4：验证

```bash
# 1) 环境变量注入计数（只计数不回显值），两容器各期望 5
docker exec aid-agent-api2 sh -c 'env | grep -c ^WEB_PUSH'
docker exec aid-agent-background2 sh -c 'env | grep -c ^WEB_PUSH'
# 2) dispatcher 启动无报错
docker logs --since 5m aid-agent-background2 2>&1 | grep -iE 'notification|dispatcher' | tail -20
```

浏览器验证（租户账号登录 agent2.aidingyi.cn）：

1. 头像菜单 → 通知设置：不再显示「服务端暂未开放浏览器通知」，出现「开启本浏览器通知」。
2. 点击开启 → 浏览器弹权限框允许 → 显示「本浏览器已开启」。
3. 点「发送测试通知」→ 应弹出系统通知（提示语只表示入队，以实际弹通知为准）。

分别用 Edge（Windows）和 Safari（macOS）各验一轮。

## 步骤 5：真实到达验收

1. 发起一个任务，**关闭该站点的全部标签页**，等待任务终态。
2. 应收到系统通知「`${会话名称}相关任务已完成/执行失败`」。
3. 点击通知 → 未登录则先登录 → 自动回到原会话。
4. 历史任务不补发；进度/取消/结算不推送，属预期。

## 已知边界（不要误判为故障）

- **Chrome 不支持**：国内 Chrome 浏览器自身连不上 FCM，订阅都无法建立，属浏览器侧限制（国内任何网站包括 Boss直聘 的大陆 Chrome 用户同样收不到 web push）。用户侧换 Edge/Firefox/Safari 即可。
- Safari 接收时机受系统休眠/浏览器进程影响；Safari 要求收到 push 必须展示通知（userVisibleOnly）。
- 服务器发送失败自动重试（默认最多 6 次、TTL 24h）；404/410 视为订阅失效自动清理，用户重新开启即可。
- 仅 Edge/Safari/Firefox 开放；桌面客户端菜单里通知入口是隐藏的，需用浏览器访问。

## 回退

```bash
cd /var/www/agent2
sed -i '/^WEB_PUSH_/d' .env
docker compose -f docker-compose.test.yml up -d --force-recreate aid-agent-api aid-agent-background
```

四张表、历史 delivery 记录和私钥文件均可保留，不影响其他功能；重新启用按本文档步骤 2-3 重来（公钥不变则无需用户重新订阅）。

## 故障速查

| 现象 | 排查 |
|---|---|
| 页面仍显示「服务端暂未开放」 | env 计数是否为 5；公私钥是否同批生成（适配器校验公钥=私钥推导值，不匹配则 capabilities 关闭）；容器日志有无 import/配置报错 |
| 测试提示成功但没弹通知 | 查 background 日志 delivery 结果；Chrome 订阅失败属预期边界 |
| 权限弹窗没出现或被拒 | 浏览器站点设置里手动允许通知后重新开启；权限拒绝后前端不会重复申请 |
| 通知收到但点击 404/回不去 | 核验 `/api/notifications/messages/{ref}` 权限；会话已删除会明确提示，不跳转 |
