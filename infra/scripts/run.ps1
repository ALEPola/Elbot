Set-StrictMode -Version Latest
$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $PSScriptRoot '..\..')
if (-not (Test-Path '.venv')) { python -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install -U pip wheel
& ".\.venv\Scripts\pip.exe" install -r requirements.txt
& ".\.venv\Scripts\pip.exe" install -e .
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
Write-Host ">> Edit .env to set DISCORD_TOKEN if you haven't."
& ".\.venv\Scripts\python.exe" -m elbot.main
