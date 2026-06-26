# 探针 PaddleOCR layout-parsing API：发一次请求，把完整响应 dump 成 JSON 文件方便分析。
# 用法：powershell -ExecutionPolicy Bypass -NoProfile -File scripts/probe-paddleocr.ps1

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutDir = Join-Path $ScriptDir '..\debug-out'
$OutDir = (New-Object -TypeName System.IO.DirectoryInfo -ArgumentList $OutDir).FullName
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Log([string]$m) { [Console]::Error.WriteLine("[probe-paddleocr] $m") }

# 读 .env 拿 PaddleOCR 配置
$envFile = 'c:\repos\aid-work-agent\.env'
$apiUrl = ''; $token = ''
foreach ($line in Get-Content $envFile -Encoding UTF8) {
    if ($line -match '^\s*PADDLEOCR_DOC_PARSING_API_URL\s*=\s*(.+?)\s*$') { $apiUrl = $Matches[1].Trim().Trim('"').Trim("'") }
    if ($line -match '^\s*PADDLEOCR_ACCESS_TOKEN\s*=\s*(.+?)\s*$') { $token = $Matches[1].Trim().Trim('"').Trim("'") }
}
if (-not $apiUrl) { throw 'PADDLEOCR_DOC_PARSING_API_URL 未配置' }
if (-not $token) { throw 'PADDLEOCR_ACCESS_TOKEN 未配置' }
Log ("API URL: {0}" -f $apiUrl)
Log ("Token: {0}..." -f $token.Substring(0, [Math]::Min(8, $token.Length)))

# 找最新一张 Step 1 的截图
$stateFile = Join-Path $OutDir 'debug-state.json'
if (-not (Test-Path $stateFile)) { throw '请先跑 debug-navigate.ps1 -Step screenshot' }
$state = Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
$imgPath = $state.png_path
if (-not $imgPath -or -not (Test-Path $imgPath)) { throw "截图不存在：$imgPath" }
Log ("使用截图：{0}" -f $imgPath)

# 读图为 base64
$bytes = [System.IO.File]::ReadAllBytes($imgPath)
$b64 = [Convert]::ToBase64String($bytes)
Log ("截图大小：{0} bytes, base64 长度：{1}" -f $bytes.Length, $b64.Length)

# 构造请求体（参考 src/tools/ocr/ocr_tool.py 的 _make_paddleocr_request）
$body = @{
    file = $b64
    fileType = 1   # 1=Image
} | ConvertTo-Json -Depth 5 -Compress

# 调用 API（HttpClient + 显式 UTF-8 decode）
Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds(300)
try {
    $content = New-Object System.Net.Http.StringContent($body, [System.Text.Encoding]::UTF8, 'application/json')
    $req = New-Object System.Net.Http.HttpRequestMessage('Post', $apiUrl)
    $req.Headers.Add('Authorization', "token $token")
    $req.Headers.Add('Client-Platform', 'official-skill')
    $req.Content = $content

    Log '发送请求...'
    $respMsg = $client.SendAsync($req).Result
    $rawBytes = $respMsg.Content.ReadAsByteArrayAsync().Result
    $jsonStr = [System.Text.Encoding]::UTF8.GetString($rawBytes)
    Log ("HTTP 状态：{0}, 响应长度：{1} bytes" -f [int]$respMsg.StatusCode, $rawBytes.Length)

    $outFile = Join-Path $OutDir ('paddleocr_probe_' + (Get-Date -Format 'HHmmss') + '.json')
    [System.IO.File]::WriteAllText($outFile, $jsonStr, [System.Text.Encoding]::UTF8)
    Log ("完整响应保存到：{0}" -f $outFile)

    # 简要看下结构
    $resp = $jsonStr | ConvertFrom-Json
    Log ("顶层字段：{0}" -f (($resp.PSObject.Properties.Name) -join ', '))
    if ($resp.errorCode -ne 0) {
        Log ("API 报错：errorCode={0} errorMsg={1}" -f $resp.errorCode, $resp.errorMsg)
    } else {
        $pages = $resp.result.layoutParsingResults
        Log ("layoutParsingResults 页数：{0}" -f $pages.Count)
        if ($pages.Count -gt 0) {
            $page0 = $pages[0]
            Log ("  page[0] 字段：{0}" -f (($page0.PSObject.Properties.Name) -join ', '))
            if ($page0.prunedResult) {
                Log ("  prunedResult 字段：{0}" -f (($page0.prunedResult.PSObject.Properties.Name) -join ', '))
                $pruned = $page0.prunedResult
                # 应用层各模型版本字段名不同，列举所有 array 字段方便后续定位 bbox 来源
                foreach ($prop in $pruned.PSObject.Properties) {
                    if ($prop.Value -is [Array]) {
                        Log ("    {0}: array, len={1}, 第一项类型={2}" -f $prop.Name, $prop.Value.Count, ($prop.Value[0].GetType().Name))
                        if ($prop.Value.Count -gt 0) {
                            $first = $prop.Value[0]
                            if ($first -is [System.Management.Automation.PSCustomObject]) {
                                Log ("      第一项字段：{0}" -f (($first.PSObject.Properties.Name) -join ', '))
                            }
                        }
                    } else {
                        Log ("    {0}: {1}" -f $prop.Name, $prop.Value.GetType().Name)
                    }
                }
            }
        }
    }
} finally {
    $client.Dispose()
}
