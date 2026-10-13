param([string]$Output = 'dist/academic-os-v2-source.zip')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& (Join-Path $projectRoot '.venv/Scripts/python.exe') -m academic_os.cli package $Output --source $projectRoot
exit $LASTEXITCODE
