param(
    [Parameter(Mandatory = $true)][ValidateSet('install', 'start', 'stop', 'remove', 'status')][string]$Action,
    [string]$Config = 'config.json',
    [string]$Account
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$serviceArgs = @('-m', 'academic_os.windows_service', $Action)
if ($Action -eq 'install') {
    if (-not $Account) { throw 'Specify the same Windows account that owns the Credential Manager secrets (-Account COMPUTER\owner).' }
    $resolvedConfig = (Resolve-Path -LiteralPath $Config).Path
    $serviceArgs += @('--config', $resolvedConfig, '--account', $Account)
}
& (Join-Path $projectRoot '.venv/Scripts/python.exe') @serviceArgs
exit $LASTEXITCODE
