# E0 实验起步记录（2026-09-06）

状态：E0 代码与开发自测完成，独立测试与审查通过；真实安卓截图与 GLM 图文调用尚未完成。

关联：[实验手册](../../plans/marketing-call-agent-experiment-runbook.md)。

## 本轮范围

实现独立 doctor/observe CLI，验证 ADB 检测、截图读取、图文定位协议、坐标转换与绘框。不实现点击、拨号、音频或云图适配。生产启动、配置与数据库保持隔离。

## 本机条件

- 宿主 Python 3.12.9，采用兼容版本记录，不要求替换全局 Python。
- PATH、常见 Android SDK 路径及应用目录未发现 ADB/Android Studio。
- 进程未配置 MCA_VISION_API_KEY/MCA_VISION_ENDPOINT/ZHIPU_API_KEYS；仓库 dotenv 未发现 MCA_VISION/ZHIPU 配置项。仅检查配置项存在性，不记录密钥值。
- 无真机或模拟器截图证据；真实设备/模型实验结论为 BLOCKED，不能用 mock 通过代替。

## 验证结果

- 主控接手开发子智能体因用量限制中断的代码，补齐共享异常类型、端点约束、UTF-8 写入、Windows 操作说明和隔离依赖。
- 开发自测 32 个用例通过；独立测试补充边界用例后 37 passed，无警告。主控复跑 37 passed（0.37 秒）。全部使用合成 PNG 与 mock ADB/HTTP，不计入真实 E0。
- `doctor` 在本机返回 `BLOCKED / ADB_MISSING`，符合实际；CLI 帮助和 E0 模块导入成功。
- 已提供 Windows PowerShell＋小米手机 USB 调试操作说明，详见 [工具 README](../../../experiments/marketing_call_agent/README.md)。
- 独立 CodeReview 完成，未发现 P0/P1；审查者复跑 37 passed。4 个实现模块 AST/import 通过，生产启动链路未改动。
- 文档链接与 git diff --check 通过。本轮不提交或推送代码。

## 下一次真实实验

准备官方 Android Platform Tools、启动模拟器或连接已授权 USB 调试的手机；使用无敏感数据的系统设置页面，配置 GLM 图文 API。按工具 README 先运行 doctor，再运行 observe 获取目标框供人工对照。此阶段不点击。
