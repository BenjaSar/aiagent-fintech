$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$pidFile = Join-Path $root '.local\anvil.pid'
if (Test-Path $pidFile) {
    Stop-Process -Id ([int](Get-Content $pidFile)) -ErrorAction SilentlyContinue
    Remove-Item $pidFile
}
