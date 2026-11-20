param(
    [string]$AltoVersion = '0.0.21'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$bundlerDirectory = Join-Path $root '.local\bundler'

New-Item -ItemType Directory -Force $bundlerDirectory | Out-Null
Push-Location $bundlerDirectory
try {
    if (-not (Test-Path 'package.json')) {
        npm init -y | Out-Null
    }
    npm install --save-exact "@pimlico/alto@$AltoVersion"
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
}
finally {
    Pop-Location
}
Write-Host "Alto $AltoVersion installed under $bundlerDirectory"
