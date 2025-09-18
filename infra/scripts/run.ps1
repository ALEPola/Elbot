Set-StrictMode -Version Latest

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if ($env:PYTHONPATH) {
  $env:PYTHONPATH = "$root\src;$env:PYTHONPATH"
} else {
  $env:PYTHONPATH = "$root\src"
}
$python = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
& $python -m elbot.cli run @args
