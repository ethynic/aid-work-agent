# E0 安卓视觉实验起步工具

仅实现 `doctor` / `observe`，没有点击、拨号或生产集成。遵循 [实验手册](../../docs/plans/marketing-call-agent-experiment-runbook.md)。运行截图前，现场人员打开无敏感测试页（系统设置也可），固定竖屏，并确认可发送给官方视觉 API。真实客户页面不要使用本实验工具；本版仅保存无敏感实验数据，文件权限不等于加密。

## 安装

在仓库根目录执行，推荐 Python 3.11；本机实际开发验证 Python 3.12.9：

```bash
python3 -m venv experiments/marketing_call_agent/.venv
experiments/marketing_call_agent/.venv/bin/pip install -r experiments/marketing_call_agent/requirements.lock
```

另安装 Android SDK Platform Tools，连接已授权 USB 调试的模拟器或手机。用户手动确认设备 serial，不自动选择设备。`doctor` 只使用标准库，未装图像依赖也可执行：

```bash
python3 -m experiments.marketing_call_agent.cli doctor --adb /absolute/path/to/adb --serial emulator-5554
```

`doctor` PASS 仅表示选定设备在线；单独报告 API 配置是否存在，不验证模型权限，不输出设备清单或密钥。当前宿主机未提供 ADB、设备与视觉凭证，因此真实 E0 仍 BLOCKED。

## 一次截图定位

本机安全设置 `MCA_VISION_API_KEY` 和 `MCA_VISION_ENDPOINT` 环境变量（样例见 config.example.env；工具不读项目 .env）。端点必须是官方 HTTPS 完整 chat/completions 地址。模型固定 `glm-5.3-flash`，不自动降级。是否接受该模型图文请求仍待真实账号验证。

```bash
experiments/marketing_call_agent/.venv/bin/python -m experiments.marketing_call_agent.cli observe \
  --adb /absolute/path/to/adb --serial emulator-5554 \
  --target '搜索入口' --confirm-test-content \
  --output /absolute/existing/private-parent/new-run
```

输出目录必须在仓库外、尚不存在，父目录提前创建。POSIX 上目录权限 0700、文件 0600；Windows 上请使用当前用户专用目录（chmod 不保证 Windows ACL），保存 original.png、input.png、overlay.png、result.json、events.jsonl。只发送等比缩小后的全图，最大边 1280；不裁剪、旋转、不乘 DPI；异常 JSON、旧 frame_id、歧义和越界框均拒绝。没有任何 ADB input 命令。网络错误不回显原始响应，不自动重试。

成功生成框仍返回 `BLOCKED / HUMAN_OVERLAY_REVIEW_REQUIRED`：人工打开 overlay.png 对照实际目标，将正确/错误写入实验报告后才可认定真实 E0 通过。CLI 不把模型自报目标存在当成准确率证明。退出码 0=PASS、1=FAIL、2=BLOCKED。缺设备、权限、凭证、受保护/黑屏截图均不能作为通过；当前仅记录 PNG 尺寸，前台包名、系统版本、wm size/density 与方向详细遥测留待下一步设备适配，人工实验报告补录。

## 离线验证

```bash
PATH="$PWD/experiments/marketing_call_agent/.venv/bin:$PATH" bash scripts/dev_test.sh \
  -c experiments/marketing_call_agent/pytest.ini \
  experiments/marketing_call_agent/tests --confcutdir=experiments/marketing_call_agent -p no:cacheprovider -q
```

测试合成白色 PNG、mock ADB/HTTP，不连接真实设备或 API。测试中的 `real_adb_real_model` 字段只是被测真实路径逻辑的输出，不是实测证据。真实执行只有 observe 路径；不提供可冒充真实来源的 mock CLI 开关。

下一步用户准备：ADB＋已授权设备、无敏感测试页面、视觉 API 凭证、仓库外证据位置。通过一张真实定位与人工复核后，再进入 E1 单步点击、页面新鲜度校验与后置条件；音频 E3 独立等待硬件，不在本次代码内。

## Windows + 小米真机（后续现场执行）

1. 手机“设置 → 关于手机 → 详细信息与规格”，连续点击 OS/MIUI 版本进入开发者模式；返回“更多设置 → 开发者选项”，开启 USB 调试。不同 MIUI/HyperOS 菜单名可能不同。用支持数据传输的数据线连接电脑，在手机上确认本电脑的 RSA 调试授权。不需要 Root 或 Bootloader 解锁。
2. 下载官方 [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)，解压，例如 `C:\Android\platform-tools`。运行 `adb.exe devices`，指定状态为 `device` 的序列号。`unauthorized` 时解锁手机确认授权；列表为空时检查线材、USB 端口和 Windows 设备管理器的 ADB 驱动。
3. 当前 E0 不模拟触摸，不需要额外开放点击权限。未来 E1 若设备拒绝模拟输入，再根据实际系统提示检查厂商安全设置，不能把打开所有开发选项当作前置步骤。

在仓库根目录打开 PowerShell：

```powershell
py -3.12 -m venv experiments/marketing_call_agent/.venv
$python = Join-Path $PWD 'experiments/marketing_call_agent/.venv/Scripts/python.exe'
& $python -m pip install -r experiments/marketing_call_agent/requirements.lock
$adb = 'C:\Android\platform-tools\adb.exe'
& $adb devices
$serial = Read-Host '输入本次测试设备序列号'
& $python -m experiments.marketing_call_agent.cli doctor --adb $adb --serial $serial
```

设置当前 PowerShell 会话的 API 配置；不写入仓库、不在命令历史中粘贴实际密钥：

```powershell
$env:MCA_VISION_ENDPOINT = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
$secret = Read-Host 'GLM API Key' -AsSecureString
$env:MCA_VISION_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
$parent = Join-Path $env:LOCALAPPDATA 'MarketingCallE0'
New-Item -ItemType Directory -Force $parent | Out-Null
$output = Join-Path $parent ('run-' + [guid]::NewGuid().ToString('N'))
& $python -m experiments.marketing_call_agent.cli observe --adb $adb --serial $serial --target '搜索入口' --confirm-test-content --output $output
```

执行前手机停在无敏感内容的系统设置页；成功产出后人工查看 `$output\overlay.png`。`HUMAN_OVERLAY_REVIEW_REQUIRED` 是等待人工核对框位置，不是模型调用失败。密钥只在当前进程环境使用，完成后 `Remove-Item Env:MCA_VISION_API_KEY`。

Windows 无 Bash 时离线测试采用统一脚本宿主机分支的等价命令：

```powershell
& $python -m pytest -c experiments/marketing_call_agent/pytest.ini experiments/marketing_call_agent/tests --confcutdir=experiments/marketing_call_agent -p no:cacheprovider -q
```

本轮只在 macOS/Python 3.12 完成离线检查，Windows 命令、手机截图及真实 API 仍需现场验证。小米设置来源：[官方开启开发者选项说明](https://www.mi.com/global/support/faq/details/KA-168765/)、[Android 真机连接与 Windows 驱动](https://developer.android.google.cn/studio/run/device?hl=en)。
