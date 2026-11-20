$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$enclaveRoot = Join-Path $repositoryRoot ".local\enclaves"
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$script = Join-Path $repositoryRoot "scripts\run_enclave_node.py"
$pidFile = Join-Path $enclaveRoot "enclaves.pid"

if (-not (Test-Path (Join-Path $enclaveRoot "layout.json"))) {
    throw "Enclaves are not provisioned. Run scripts\provision_local_enclaves.py first."
}
if (Test-Path $pidFile) {
    throw "Enclaves appear to be running (pid file exists). Run stop-local-enclaves.ps1 first."
}

$group = Get-Content (Join-Path $enclaveRoot "layout.json") -Raw | ConvertFrom-Json
$identifiers = $group.ports.PSObject.Properties.Name
$processIdentifiers = @()
foreach ($identifier in $identifiers) {
    $log = Join-Path $enclaveRoot "node-$identifier.log"
    $process = Start-Process -FilePath $python `
        -ArgumentList @($script, "--identifier", $identifier) `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $processIdentifiers += $process.Id
}
$processIdentifiers | Set-Content $pidFile
Write-Host "Started enclaves $($identifiers -join ', ') (process ids $($processIdentifiers -join ', '))."

