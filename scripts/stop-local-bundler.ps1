$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$pidFile = Join-Path $root '.local\bundler\alto.pid'

if (-not (Test-Path $pidFile)) {
    Write-Host 'No bundler pid file; nothing to stop.'
    return
}
$processIdentifier = [int](Get-Content $pidFile)
if (Get-Process -Id $processIdentifier -ErrorAction SilentlyContinue) {
    taskkill /PID $processIdentifier /T /F | Out-Null
}
Remove-Item $pidFile
Write-Host 'Stopped the local bundler.'
