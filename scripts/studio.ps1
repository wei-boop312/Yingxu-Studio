param([Parameter(ValueFromRemainingArguments=$true)][string[]]$StudioArgs)
$ErrorActionPreference = 'Stop'
$StudioRoot = Split-Path -Parent $PSScriptRoot
$StudioPython = Join-Path $StudioRoot 'apps\commerce\.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $StudioPython)) {
    throw '请先按 docs/GETTING_STARTED.md 配置电商工程的独立 Python 环境。'
}
& $StudioPython (Join-Path $PSScriptRoot 'studio.py') @StudioArgs
exit $LASTEXITCODE
