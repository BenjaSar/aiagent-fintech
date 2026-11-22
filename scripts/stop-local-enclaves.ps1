$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repositoryRoot ".local\enclaves\enclaves.pid"
$enclaveSetsRoot = Join-Path $repositoryRoot ".local\enclave-sets"

if (Test-Path $enclaveSetsRoot) {
    foreach ($rotationPidFile in Get-ChildItem -Path $enclaveSetsRoot -Recurse -Filter "enclaves.pid") {
        foreach ($processIdentifier in Get-Content $rotationPidFile.FullName) {
            Stop-Process -Id ([int]$processIdentifier) -ErrorAction SilentlyContinue
        }
        Remove-Item $rotationPidFile.FullName
    }
}

if (-not (Test-Path $pidFile)) {
    Write-Host "No enclave pid file; nothing to stop."
    return
}
foreach ($processIdentifier in Get-Content $pidFile) {
    Stop-Process -Id ([int]$processIdentifier) -ErrorAction SilentlyContinue
}
Remove-Item $pidFile
Write-Host "Stopped local enclaves."
