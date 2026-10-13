param([string]$Python = 'python', [switch]$Development)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& $Python -m venv (Join-Path $projectRoot '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ virtual environment creation failed' }
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip bootstrap failed' }
$installTarget = if ($Development) { '.[dev]' } else { '.' }
& $venvPython -m pip install -c requirements.lock.txt -e $installTarget
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Write-Host 'Installed. Initialize: .venv/Scripts/python.exe -m academic_os.cli --config config.json init'
