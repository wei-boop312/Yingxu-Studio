$ErrorActionPreference = 'Stop'
& 'D:\python\python.exe' -B (Join-Path $PSScriptRoot 'start_offline_ai.py') --start
if ($LASTEXITCODE -ne 0) { throw 'Local model was not started. Existing service or mismatched files are preserved.' }
Write-Output 'Model process launched. Check http://127.0.0.1:18189/health before using the plugin.'
