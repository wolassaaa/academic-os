param([string]$Config = 'config.json')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& (Join-Path $projectRoot '.venv/Scripts/python.exe') -m academic_os.cli --config $Config run
exit $LASTEXITCODE
