Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if ($env:PYTHONPATH) {
  $env:PYTHONPATH = "$root\src;$env:PYTHONPATH"
} else {
  $env:PYTHONPATH = "$root\src"
}
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
& $python -m elbot.cli install @args

$wrapperCmd = Join-Path $root '.\.venv\Scripts\elbotctl.cmd'
$wrapperPs1 = Join-Path $root '.\.venv\Scripts\elbotctl.ps1'
if (Test-Path (Join-Path $root '.\.venv\Scripts\python.exe')) {
  Set-Content -Path $wrapperCmd -Value "@echo off`r`n%~dp0python.exe -m elbot.cli %*" -Encoding ASCII
  Set-Content -Path $wrapperPs1 -Value "param([string[]]`$Args)`r`n`$scriptDir = Split-Path -Parent `$MyInvocation.MyCommand.Path`r`n& `$scriptDir\python.exe -m elbot.cli `$Args" -Encoding UTF8
}
