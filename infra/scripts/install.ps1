Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "*** Elbot Setup (Windows) ***"

if (-not (Test-Path '.venv')) {
  python -m venv .venv
}

$py = Join-Path $root '.\.venv\Scripts\python.exe'
$pip = Join-Path $root '.\.venv\Scripts\pip.exe'

& $py -m pip install -U pip wheel

if (Test-Path 'pyproject.toml') {
  & $pip install -e .
} elseif (Test-Path 'requirements.txt') {
  & $pip install -r requirements.txt
}

if (-not (Test-Path '.env') -and (Test-Path '.env.example')) {
  Copy-Item '.env.example' '.env'
}

function Update-EnvVar([string]$Name, [string]$Value) {
  $content = Get-Content '.env' -Raw
  if ($content -match "(?m)^$Name=") {
    $content = [regex]::Replace($content, "(?m)^$Name=.*$", "$Name=$Value")
  } else {
    $content += "`n$Name=$Value"
  }
  Set-Content '.env' $content
}

Write-Host "Configuring .env ..."
$token = Read-Host 'Discord bot token'
if ($token) { Update-EnvVar 'DISCORD_TOKEN' $token }
$openai = Read-Host 'OpenAI API key (optional)'
if ($openai) { Update-EnvVar 'OPENAI_API_KEY' $openai }

Write-Host "Installing Windows service ..."
& $py -m elbot.service_install

Write-Host "Done. The Elbot service is installed and starting automatically."
Write-Host "Use 'elbot-install-service --remove' to uninstall."

