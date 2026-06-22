#requires -Version 5.1
<#
.SYNOPSIS
  服务端 wecom_personal_rpa 渠道 HMAC 回调冒烟测试（不需要客户端）。
  用与服务端 auth.compute_signature 字节对字节一致的 HMAC-SHA256 签名，POST 一条 message 事件到 callback。

  前置：
    1. 服务端已部署本渠道代码（提交 d7b8d72 起）并启动。
    2. 服务端已配置环境变量 RPA_SECRET_KEY=<强随机串>。
    3. 已注册客户端，拿到 client_id 与明文 client_secret（注册接口仅返回一次）。

.EXAMPLE
  powershell scripts\test-server-callback.ps1 `
    -ServerUrl "https://agent.example.com" -TenantId "t_demo" -ConfigId "cfg_demo" `
    -ClientId "client_xxx" -Secret "明文client_secret"

  预期：HTTP 200 {"result":"accepted"}；再跑同 event_id 仍 accepted 但服务端日志显示「重复事件已跳过」。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [Parameter(Mandatory = $true)][string]$TenantId,
    [Parameter(Mandatory = $true)][string]$ConfigId,
    [Parameter(Mandatory = $true)][string]$ClientId,
    [Parameter(Mandatory = $true)][string]$Secret,
    [string]$AccountId = 'wecom_account_smoke',
    [string]$EventId = ('evt_smoke_' + ([Guid]::NewGuid().ToString('N').Substring(0, 12))),
    [string]$Text = '来自 test-server-callback.ps1 的冒烟消息'
)
$ErrorActionPreference = 'Stop'

# 1. 构造 body（对齐 src/channels/wecom_personal_rpa/schemas.py RpaCallbackEnvelope）
$envelope = [ordered]@{
    event_id    = $EventId
    client_id   = $ClientId
    account_id  = $AccountId
    event_type  = 'message'
    occurred_at = (Get-Date -Format 'o')
    payload     = [ordered]@{
        conversation_id      = 'conv_smoke'
        conversation_type    = 'external_user'
        sender_display_name  = '冒烟测试'
        sender_stable_id     = $null
        message_type         = 'text'
        text                 = $Text
        attachments          = @()
    }
}
$body = $envelope | ConvertTo-Json -Depth 10 -Compress

# 2. 签名：HMAC-SHA256(client_id + timestamp + nonce + raw_body, secret)，hex 小写
#    UTF-8 编码对拼接同态：enc(A+B+C+D) == enc(A)+enc(B)+enc(C)+enc(D)，
#    故与服务端 _to_bytes(cid)+_to_bytes(ts)+_to_bytes(nonce)+raw_body 字节对字节一致。
$enc = [Text.Encoding]::UTF8
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
$nonce = [Guid]::NewGuid().ToString('N')
$msgBytes = $enc.GetBytes($ClientId + $timestamp + $nonce + $body)
$bodyBytes = $enc.GetBytes($body)   # 发送时用与签名完全一致的原始字节
$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = $enc.GetBytes($Secret)
$sig = [BitConverter]::ToString($hmac.ComputeHash($msgBytes)).Replace('-', '').ToLowerInvariant()

# 3. POST callback
$url = $ServerUrl.TrimEnd('/') + "/t/$TenantId/wecom_personal_rpa/callback/$ConfigId"
$headers = @{
    'X-Client-Id' = $ClientId
    'X-Timestamp' = $timestamp
    'X-Nonce'     = $nonce
    'X-Signature' = $sig
}
Write-Host "POST $url"
Write-Host "event_id = $EventId"
Write-Host "签名输入 = client_id($ClientId) + ts($timestamp) + nonce($nonce) + body($($bodyBytes.Length) B)"

try {
    $resp = Invoke-WebRequest -Uri $url -Method Post -Headers $headers -Body $bodyBytes `
        -ContentType 'application/json' -UseBasicParsing -ErrorAction Stop
    Write-Host "HTTP $($resp.StatusCode) [OK]" -ForegroundColor Green
    Write-Host $resp.Content
    Write-Host ""
    Write-Host "提示：客户端离线时，agent 回复会落入服务端 wecom_rpa_action_outbox（可在管理端审计/DB 查）。"
}
catch {
    # 兼容 PS5.1(WebException) 与 PS7(HttpResponseException)
    $resp = $_.Exception.Response
    if ($resp) {
        $code = try { [int]$resp.StatusCode } catch { 0 }
        Write-Host "HTTP $code [FAIL]" -ForegroundColor Yellow
        $bodyText = $null
        try { $bodyText = $_.ErrorDetails.Message } catch {}
        if ([string]::IsNullOrWhiteSpace($bodyText)) {
            try {
                $stream = $resp.GetResponseStream()
                $sr = New-Object IO.StreamReader($stream)
                $bodyText = $sr.ReadToEnd(); $sr.Close()
            } catch {}
        }
        Write-Host $bodyText
        Write-Host ""
        Write-Host "排查：401 auth_failed 多为 secret 不一致/时钟漂移>300s/client_id 拼错；400 bad_request 为信封字段不符。"
    }
    else {
        Write-Host "请求失败（网络/URL）：$($_.Exception.Message)" -ForegroundColor Red
    }
}
