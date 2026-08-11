# PowerShell 脚本编写规范

> 适用：本项目所有 `.ps1` 脚本（微信 RPA、协会客户端、agent-desktop 构建、weixin-cli probe 等）。
>
> **背景**：PowerShell 脚本在本项目中反复出现两类系统性错误——① 字符串插值歧义；② PS 5.1 对无 BOM UTF-8 的 ANSI 误读导致中文注释/字符串炸裂解析。本规范把已踩过的坑固化成强制约定，**写脚本前先读**。

---

## 1. 文件编码：必须 UTF-8 with BOM（最高优先级）

**根因**：Windows PowerShell 5.1（`powershell.exe`）的 `ParseFile` / 脚本加载默认按当前 ANSI 代码页（中文系统=GBK/cp936）读取**无 BOM** 的 UTF-8 文件。脚本里的中文注释、中文字符串参数会被按 GBK 误解码，UTF-8 多字节序列被切碎，**破坏紧随其后的 ASCII token**，抛出"表达式不识别 token x"、"Try 缺少 Catch"等**假语法错**——错误行号指向中文字符附近的合法代码，极具迷惑性。

**症状特征**（命中即编码问题，不是语法问题）：
- 报错指向 `Write-Host` 行的某个字母 token（如 `x`、`vision`），但该行代码看起来完全合法；
- 报"Try 语句缺少 Catch/Finally"、"语句块缺少 }"，但 try/catch/花括号明明配对；
- 同样的代码在 PowerShell 7（`pwsh`，默认 UTF-8 无 BOM）下不报错，只在 5.1 下报错。

**强制要求**：

| 项 | 要求 |
|---|---|
| 文件编码 | **UTF-8 with BOM**（`EF BB BF` 三字节头）。含中文的 ps1 必须带 BOM |
| 纯 ASCII 脚本 | 可不带 BOM，但**统一带 BOM** 最省心 |
| 写文件工具 | Write 工具默认写无 BOM UTF-8，**写完含中文的 ps1 后必须补 BOM**（见 §5 命令） |

**验证 BOM**：
```bash
head -c3 path/to.ps1 | od -An -tx1   # 应输出 ef bb bf
```

---

## 2. 字符串插值：禁用歧义写法，统一用 `-f` 格式化

PowerShell 双引号字符串里的 `$` 插值有多种歧义陷阱。**写 `Write-Host` / 日志输出时，禁用裸插值拼接，改用 `-f` 运算符**。

### 2.1 禁止：`${var}x` 紧跟字母的写法

```powershell
# ❌ 禁止：${w}x${h} —— PS 5.1 解析器对 "${w}x" 的 token 边界判断不稳，
#           叠加无 BOM 中文误读时必炸（已在 5 个 probe 脚本中出现过）
Write-Host "rect=($winLeft,$winTop,${w}x${h})"

# ✅ 正确：用 -f 格式化
Write-Host ("rect=({0},{1},{2}x{3})" -f $winLeft,$winTop,$w,$h)
```

### 2.2 禁止：双引号里混用裸 `$var` 与 `$($expr)`

```powershell
# ⚠️ 不推荐：裸变量 $winLeft 和子表达式 $($loc.found) 混用，
#            阅读时易错，且 $obj.Property 在双引号里不会自动展开属性
Write-Host "found=$($loc.found) png=($x,$y)"

# ✅ 推荐：整句用 -f
Write-Host ("found={0} png=({1},{2})" -f $loc.found,$x,$y)
```

### 2.3 属性访问必须用 `$()` 包裹

```powershell
# ❌ 错误：双引号里 $obj.Property 只输出 $obj 本身（属性不展开）
Write-Host "hwnd=$main.Hwnd"          # 输出 "hwnd=<对象类型>.Hwnd"

# ✅ 正确：要么 $() 包裹，要么 -f
Write-Host "hwnd=$($main.Hwnd)"
Write-Host ("hwnd={0}" -f $main.Hwnd)
```

### 2.4 判定规则

| 场景 | 写法 |
|---|---|
| `Write-Host` / 日志输出含变量 | **一律 `-f` 格式化** |
| 简单单变量字符串（无拼接歧义） | 可用 `"$var"` |
| 字符串里要拼一个表达式/属性 | **`$()` 包裹** 或改 `-f` |
| 含 `{` `}` 的格式化串 | `-f` 占位符 `{0}` `{1}` |

---

## 3. HTTP 调用：大 body 用 `curl.exe --data-binary @file`，禁用 `Invoke-RestMethod`

**根因**：PowerShell 5.1 的 `Invoke-RestMethod` / `Invoke-WebRequest` 对**大 base64 body**（如图片 vision 请求）会误报 400/编码错误；且对 UTF-8 处理不稳。

**强制要求**：调用外部 HTTP API（尤其含图片 base64、大 JSON body）时：

```powershell
# ✅ 正确：payload 写文件，curl.exe --data-binary 发送
$utf8NoBom = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($reqPath, $payloadJson, $utf8NoBom)
$http = & curl.exe -s -X POST $apiBase `
    -H "Authorization: Bearer $apiKey" `
    -H 'Content-Type: application/json' `
    --data-binary "@$reqPath" `
    -o $respPath -w '%{http_code}' --max-time 180
if ($LASTEXITCODE -ne 0) { throw "CURL_FAILED exit=$LASTEXITCODE" }
if ($http -ne '200') { throw "HTTP_$http" }
```

**注意**：
- 用 `curl.exe`（带 `.exe`），不要用别名 `curl`（PS 里 `curl` 默认是 `Invoke-WebRequest` 别名）；
- payload 写文件用 `UTF8Encoding($false)`（无 BOM），HTTP body 不能带 BOM；
- 检查 `$LASTEXITCODE`（curl 退出码）和 `$http`（HTTP 状态码）两个。

---

## 4. 外部 API 必须有重试 + 超时

**根因**：今天 Kimi API 返回 429（`engine_overloaded_error`），脚本直接 throw 退出，要手动重跑。外部 API 的 429/503/网络抖动是常态，脚本必须内置重试。

**强制要求**：任何外部 HTTP 调用必须有：

| 项 | 要求 |
|---|---|
| 超时 | `curl.exe --max-time <秒>` |
| 重试 | 429/503/网络错重试 N 次，指数或固定退避 |
| 失败诊断 | throw 时带上 HTTP 状态码 + 响应体前 300 字符 |

**模板**（429 重试）：
```powershell
for ($attempt = 1; $attempt -le 4; $attempt++) {
    $http = & curl.exe @curlArgs
    if ($LASTEXITCODE -ne 0) { throw "CURL_FAILED exit=$LASTEXITCODE" }
    if ($http -eq '200') { break }
    if ($http -eq '429' -and $attempt -lt 4) {
        Write-Host ("[retry] HTTP 429 (attempt {0}/4), waiting 25s..." -f $attempt)
        Start-Sleep -Seconds 25
        continue
    }
    $errBody = if (Test-Path $respPath) { Get-Content $respPath -Raw -Encoding UTF8 } else { '<no body>' }
    throw ("HTTP_{0} body={1}" -f $http, $errBody.Substring(0, [Math]::Min(300, $errBody.Length)))
}
```

---

## 5. 写完脚本后的强制检查清单

每次用 Write/Edit 写完或修改 `.ps1` 后，**必须**跑这两步，不要直接执行：

```bash
# 1. 含中文的 ps1：补 UTF-8 BOM（若没有）
f="path/to.ps1"
if ! head -c3 "$f" | od -An -tx1 | grep -q 'ef bb bf'; then
  cp "$f" "$f.nobom"
  printf '\xef\xbb\xbf' | cat - "$f.nobom" > "$f" && rm "$f.nobom"
fi
head -c3 "$f" | od -An -tx1   # 确认输出 ef bb bf

# 2. 语法检查（ParseFile，不执行）
powershell.exe -NoProfile -Command "\$errs=\$null; \$null=[System.Management.Automation.Language.Parser]::ParseFile('path/to.ps1',[ref]\$null,[ref]\$errs); if(\$errs){\$errs|%{\"L\$(\$_.Extent.StartLineNumber): \$(\$_.Message)\"}}else{'PARSE_OK'}"
```

- ParseFile 报错指向**合法行的字母 token** + 脚本含中文 → 99% 是 BOM 缺失，先补 BOM 再查；
- ParseFile 报错指向 `${var}` 或 `$()` 混用行 → 改 `-f` 格式化；
- `PARSE_OK` 后再真机执行。

---

## 6. 函数定义与调用约定

| 项 | 要求 |
|---|---|
| 函数参数 | `param([Parameter(Mandatory)][string]$X)` 显式类型 |
| 调用函数 | **不加括号、不加逗号**：`Foo $a $b`，不是 `Foo($a,$b)` |
| 返回值 | 用 `return`，不要依赖"未捕获输出即返回"（易混入 Write-Host） |
| 函数内 `Write-Host` | `Write-Host` 输出到控制台**不进管道**，不会污染返回值，但若函数靠隐式返回，须用 `return` 显式 |

```powershell
# ❌ 错误：括号+逗号（变成传一个数组参数）
Click-ScreenPoint($x, $y, $winLeft)

# ✅ 正确：空格分隔
Click-ScreenPoint $x $y $winLeft
```

---

## 7. 外部命令调用约定

调用 `curl.exe`、`git.exe` 等外部命令时用**数组 splatting**，不要拼字符串命令行：

```powershell
$curlArgs = @('-s', '-X', 'POST', $apiBase,
    '-H', "Authorization: Bearer $apiKey",
    '--data-binary', "@$reqPath",
    '-o', $respPath, '-w', '%{http_code}', '--max-time', '180')
$http = & curl.exe @curlArgs
```

- `@array` splatting 自动处理含空格参数的引号；
- 检查 `$LASTEXITCODE` 判断外部命令是否成功。

---

## 8. 已踩坑速查表（持续补充）

| 症状 | 根因 | 修复 |
|---|---|---|
| ParseFile 报"不识别 token x"，但该行合法 | 无 BOM，中文被 ANSI 误读切碎 ASCII | 补 UTF-8 BOM |
| `rect=(${w}x${h})` 报 token 错 | `${var}x` 插值歧义 + 可能叠加 BOM | 改 `-f` 格式化 |
| `"found=$($loc.found)"` 输出对象类型名 | 双引号里属性不展开（已用 $() 则正常） | 用 $() 或 -f |
| `Invoke-RestMethod` 对大 base64 报 400 | PS 5.1 IRM 对大 body 编码处理不稳 | 改 curl.exe --data-binary @file |
| 外部 API 偶发 429，脚本直接挂 | 没有重试 | 加 429 重试循环（§4） |
| `curl` 别名指向 Invoke-WebRequest | PS 默认别名 | 用 `curl.exe`（带 .exe） |
| 函数调用 `Foo($a,$b)` 参数错 | 加了括号逗号 | `Foo $a $b` 空格分隔 |
| Unicode 中文输入被 Qt 静默丢弃 | `KEYEVENTF_UNICODE` 对 Qt 无效 | 剪贴板逐字粘贴 |
