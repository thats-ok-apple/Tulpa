$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = Join-Path $repoRoot '.tmp'
$env:TMP = $env:TEMP
& "$repoRoot\.venv\Scripts\python.exe" "$repoRoot\app_mcp.py"
