$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$dependencies = @(
    @{
        Name = 'openzeppelin-contracts'
        Url = 'https://github.com/OpenZeppelin/openzeppelin-contracts.git'
        Tag = 'v5.6.1'
        Commit = '5fd1781b1454fd1ef8e722282f86f9293cacf256'
    }
)

foreach ($dependency in $dependencies) {
    $target = Join-Path $root "lib\$($dependency.Name)"
    if (-not (Test-Path $target)) {
        git clone --quiet --depth 1 --branch $dependency.Tag $dependency.Url $target
    }
    $actual = (git -C $target rev-parse HEAD).Trim()
    if ($actual -ne $dependency.Commit) {
        throw "$($dependency.Name) is at $actual, expected $($dependency.Commit)"
    }
    Write-Output "$($dependency.Name) $($dependency.Tag) verified at $actual"
}
