param(
    [int]$Port = 4337,
    [string]$RpcUrl = 'http://127.0.0.1:8545'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$bundlerDirectory = Join-Path $root '.local\bundler'
$altoScript = Join-Path $bundlerDirectory 'node_modules\@pimlico\alto\esm\cli\alto.js'
$pidFile = Join-Path $bundlerDirectory 'alto.pid'
$cast = Join-Path $root '.tools\foundry\bin\cast.exe'
$python = Join-Path $root '.venv\Scripts\python.exe'
$entryPoint = '0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108'
$fundedBalance = '0x56bc75e2d63100000'

if (-not (Test-Path $altoScript)) {
    throw 'Alto is not installed. Run scripts\install-local-bundler.ps1 first.'
}
if (Test-Path $pidFile) {
    throw 'The bundler appears to be running (pid file exists). Run stop-local-bundler.ps1 first.'
}

function Invoke-JsonRpc([string]$Url, [string]$Method, [object[]]$Parameters) {
    $body = @{ jsonrpc = '2.0'; id = 1; method = $Method; params = $Parameters } | ConvertTo-Json -Compress -Depth 5
    return Invoke-RestMethod -Uri $Url -Method Post -ContentType 'application/json' -Body $body
}

function Get-DevelopmentKey([string]$Name) {
    $path = Join-Path $bundlerDirectory $Name
    if (-not (Test-Path $path)) {
        $key = '0x' + (& $python -c 'import secrets; print(secrets.token_hex(32))')
        Set-Content -Path $path -Value $key -NoNewline
    }
    return (Get-Content $path -Raw).Trim()
}

$chainId = (Invoke-JsonRpc $RpcUrl 'eth_chainId' @()).result
if ($chainId -ne '0x7a69') {
    throw "Refusing to start: $RpcUrl reports chain id $chainId, not the local chain 31337."
}
$entryPointCode = (Invoke-JsonRpc $RpcUrl 'eth_getCode' @($entryPoint, 'latest')).result
if ($entryPointCode.Length -lt 100) {
    throw 'The EntryPoint is not deployed on the local chain. Run start-local-chain.ps1 first.'
}

$executorKey = Get-DevelopmentKey 'executor.key'
$utilityKey = Get-DevelopmentKey 'utility.key'
foreach ($key in @($executorKey, $utilityKey)) {
    $address = & $cast wallet address --private-key $key
    Invoke-JsonRpc $RpcUrl 'anvil_setBalance' @($address, $fundedBalance) | Out-Null
}

$arguments = @(
    $altoScript,
    '--entrypoints', $entryPoint,
    '--rpc-url', $RpcUrl,
    '--executor-private-keys', $executorKey,
    '--utility-private-key', $utilityKey,
    '--safe-mode', 'false',
    '--eip-7702-support', 'false',
    '--port', $Port,
    '--log-level', 'info'
)
$process = Start-Process -FilePath 'node' -ArgumentList $arguments -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $bundlerDirectory 'alto.log') `
    -RedirectStandardError (Join-Path $bundlerDirectory 'alto.err.log')
Set-Content -Path $pidFile -Value $process.Id

$bundlerUrl = "http://127.0.0.1:$Port"
$ready = $false
for ($attempt = 0; $attempt -lt 120 -and -not $ready; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $supported = (Invoke-JsonRpc $bundlerUrl 'eth_supportedEntryPoints' @()).result
        $ready = ($supported -contains $entryPoint)
    }
    catch {
        $ready = $false
    }
}
if (-not $ready) {
    throw "The bundler did not report the v0.8 EntryPoint. See $bundlerDirectory\alto.err.log"
}
Write-Host "Alto is serving the v0.8 EntryPoint at $bundlerUrl (process $($process.Id)), safe mode off."
