$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'VENV_PYTHON_NOT_FOUND'
}
& $python (Join-Path $PSScriptRoot 'app.py') @args
