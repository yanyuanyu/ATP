param(
    [string]$Distribution = 'Ubuntu',
    [string]$PipIndexUrl = 'https://pypi.org/simple'
)
$ErrorActionPreference = 'Stop'
$dockerDir = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

Get-Command wsl.exe, node.exe, npm.cmd -ErrorAction Stop | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $dockerDir '.env'))) {
    throw 'Create docker/.env from .env.example and configure the model first.'
}
$linuxDir = (& wsl.exe -d $Distribution -- wslpath -a $dockerDir).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the project in WSL.' }
Invoke-Checked 'wsl.exe' @('-d', $Distribution, '--', 'docker', 'info', '--format', '{{.ServerVersion}}')
Invoke-Checked 'wsl.exe' @('-d', $Distribution, '--', 'docker', 'compose', 'version')
Push-Location (Join-Path $dockerDir 'demo/web')
try {
    Invoke-Checked 'npm.cmd' @('ci', '--no-audit', '--no-fund')
    Invoke-Checked 'npm.cmd' @('run', 'build')
} finally { Pop-Location }
Invoke-Checked 'wsl.exe' @('-d', $Distribution, '--cd', $linuxDir, '--', 'env', "PIP_INDEX_URL=$PipIndexUrl", 'bash', 'scripts/build.sh')
Invoke-Checked 'wsl.exe' @('-d', $Distribution, '--cd', $linuxDir, '--', 'docker', 'build', '--build-arg', "PIP_INDEX_URL=$PipIndexUrl", '-t', 'atp-hackthon-demo:latest', '-f', 'demo/Dockerfile', '.')
Write-Host 'Images built. Continue with the startup steps in RUN_WINDOWS.md.'
