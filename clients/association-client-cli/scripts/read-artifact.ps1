param(
    [Parameter(Mandatory)]
    [string]$ArtifactPath
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

try {
    $artifact = Unprotect-EvidenceArtifact $ArtifactPath
    if ([string]$artifact.list_artifact_id -match '^[a-fA-F0-9]{32}$') {
        $listPath = Join-Path (Split-Path -Parent $ArtifactPath) (
            "{0}.dpapi" -f [string]$artifact.list_artifact_id)
        if (Test-Path -LiteralPath $listPath) {
            $artifact | Add-Member -NotePropertyName list_artifact `
                -NotePropertyValue (Unprotect-EvidenceArtifact $listPath) -Force
        }
    }
    $artifact | ConvertTo-Json -Depth 12 -Compress
    exit 0
} catch {
    [Console]::Error.WriteLine('ARTIFACT_READ_FAILED')
    exit 1
}
