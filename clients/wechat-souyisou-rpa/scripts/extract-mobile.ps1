[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ArtifactPath,
    [Parameter(Mandatory)][string]$AssociationName,
    [Parameter(Mandatory)][string]$PersonName
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'wechat-souyisou-lib.ps1')

$artifact = Unprotect-EvidenceArtifact $ArtifactPath
$foundResult = $artifact.found_result
if ($foundResult -and $foundResult.matched -eq $true) {
    if (
        [string]$artifact.source -eq 'result_page_unbounded' -and
        [string]$artifact.list_artifact_id -match '^[a-fA-F0-9]{32}$'
    ) {
        $resolvedArtifact = (Resolve-Path -LiteralPath $ArtifactPath).Path
        $listArtifactPath = Join-Path (Split-Path -Parent $resolvedArtifact) `
            ("{0}.dpapi" -f [string]$artifact.list_artifact_id)
        $listArtifact = Unprotect-EvidenceArtifact $listArtifactPath
        if (
            [string]$listArtifact.kind -ne 'result_page_unbounded' -or
            [string]$listArtifact.association_name -ne $AssociationName -or
            [string]$listArtifact.person_name -ne $PersonName
        ) {
            throw 'ARTIFACT_LIST_REFERENCE_INVALID'
        }
        if (Test-JudgeResult $foundResult ([string]$listArtifact.text) $PersonName) {
            [Console]::Out.WriteLine((@{
                matched=$true
                mobile=[string]$foundResult.mobile
            } | ConvertTo-Json -Compress))
            exit 0
        }
        throw 'ARTIFACT_FOUND_RESULT_INVALID'
    }
    foreach ($record in @($artifact.records)) {
        foreach ($evidence in @([string]$record.text, [string]$record.ocr_text)) {
            if (Test-JudgeResult $foundResult $evidence $PersonName) {
                [Console]::Out.WriteLine((@{
                    matched=$true
                    mobile=[string]$foundResult.mobile
                } | ConvertTo-Json -Compress))
                exit 0
            }
        }
    }
    throw 'ARTIFACT_FOUND_RESULT_INVALID'
}
foreach ($record in @($artifact.records)) {
    foreach ($evidence in @([string]$record.text, [string]$record.ocr_text)) {
        if ([string]::IsNullOrWhiteSpace($evidence)) { continue }
        $judge = Invoke-EvidenceJudge $evidence $AssociationName $PersonName $null
        if ($judge.matched -eq $true) {
            [Console]::Out.WriteLine((@{
                matched=$true
                mobile=[string]$judge.mobile
            } | ConvertTo-Json -Compress))
            exit 0
        }
    }
}
[Console]::Out.WriteLine('{"matched":false,"mobile":null}')
