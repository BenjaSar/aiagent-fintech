$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
$enclaveRoot = Join-Path $root '.local\enclaves'
$scripts = $PSScriptRoot

function Wait-ForPort([int]$Port) {
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect('127.0.0.1', $Port)
            $client.Close()
            return
        }
        catch {
            Start-Sleep -Milliseconds 200
        }
    }
    throw "Nothing is listening on port $Port"
}

function Stop-Everything {
    & (Join-Path $scripts 'stop-local-bundler.ps1')
    & (Join-Path $scripts 'stop-local-enclaves.ps1')
    & (Join-Path $scripts 'stop-local-chain.ps1')
}

Stop-Everything
if (Test-Path $enclaveRoot) {
    Remove-Item -Recurse -Force $enclaveRoot
}
$rotationRoot = Join-Path (Join-Path $root '.local') 'rotations'
if (Test-Path $rotationRoot) {
    Remove-Item -Recurse -Force $rotationRoot
}

try {
    & (Join-Path $scripts 'start-local-chain.ps1')
    & $python (Join-Path $scripts 'provision_local_enclaves.py') --identities-only
    if ($LASTEXITCODE -ne 0) { throw 'provisioning failed' }
    & (Join-Path $scripts 'start-local-enclaves.ps1')
    foreach ($port in 9101, 9102, 9103) { Wait-ForPort $port }
    & $python (Join-Path $scripts 'generate_local_key.py')
    if ($LASTEXITCODE -ne 0) { throw 'key generation failed' }
    & (Join-Path $scripts 'start-local-bundler.ps1')
    & $python (Join-Path $scripts 'run_local_drill.py')
    $drillExit = $LASTEXITCODE
}
finally {
    Stop-Everything
}
exit $drillExit
