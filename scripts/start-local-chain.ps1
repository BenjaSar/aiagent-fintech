param(
    [int]$Port = 8545
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$binaries = Join-Path $root '.tools\foundry\bin'
$workspace = Join-Path $root '.local'
$fixtures = Join-Path $root 'fixtures\base-sepolia'
$rpcUrl = "http://127.0.0.1:$Port"
$contracts = @{
    '0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108' = 'EntryPoint-v0.8.0.runtime.hex'
    '0x449ED7C3e6Fee6a97311d4b55475DF59C44AdD33' = 'SenderCreator-v0.8.0.runtime.hex'
}

New-Item -ItemType Directory -Force $workspace | Out-Null
$arguments = @('--chain-id', '31337', '--hardfork', 'cancun', '--port', $Port)
$process = Start-Process -FilePath (Join-Path $binaries 'anvil.exe') -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $workspace 'anvil.log') -RedirectStandardError (Join-Path $workspace 'anvil.err.log')
Set-Content -Path (Join-Path $workspace 'anvil.pid') -Value $process.Id

$cast = Join-Path $binaries 'cast.exe'
$ready = $false
for ($attempt = 0; $attempt -lt 50 -and -not $ready; $attempt++) {
    Start-Sleep -Milliseconds 200
    & $cast chain-id --rpc-url $rpcUrl 2>$null | Out-Null
    $ready = ($LASTEXITCODE -eq 0)
}
if (-not $ready) { throw 'anvil did not become ready' }

foreach ($address in $contracts.Keys) {
    $code = (Get-Content (Join-Path $fixtures $contracts[$address]) -Raw).Trim()
    $request = @{ jsonrpc = '2.0'; id = 1; method = 'anvil_setCode'; params = @($address, $code) } | ConvertTo-Json -Compress
    $response = Invoke-RestMethod -Uri $rpcUrl -Method Post -ContentType 'application/json' -Body $request
    if ($response.error) { throw "anvil_setCode failed for ${address}: $($response.error.message)" }
}
Write-Output "local chain ready at $rpcUrl (chain id 31337, process $($process.Id))"
