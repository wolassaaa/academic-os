param([string]$Python = '', [string]$OutputRoot = 'dist/portable', [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$resolvedOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputRoot)
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
if (-not $resolvedOutput.StartsWith($distRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must be a subdirectory of project dist' }
$applicationOutput = Join-Path $resolvedOutput 'AcademicOS'
if ((Test-Path -LiteralPath (Join-Path $applicationOutput 'config.json')) -or (Test-Path -LiteralPath (Join-Path $applicationOutput 'data')) -or @(Get-ChildItem -LiteralPath $applicationOutput -File -Filter '*.sqlite*' -ErrorAction SilentlyContinue).Count) {
    throw 'Refusing to overwrite application output containing user configuration or data. Choose a new OutputRoot.'
}
if (-not $Python) { $Python = Join-Path $projectRoot '.venv/Scripts/python.exe' }
if (-not $SkipBuild) {
    & $Python scripts/export-oauth-client.py --output build/oauth-client.json
    if ($LASTEXITCODE -ne 0) { throw 'Release requires the application OAuth client. Do not ship an unconfigured login button.' }
    & $Python -m PyInstaller --noconfirm --clean --onedir --console --name AcademicOS --distpath $OutputRoot --workpath build/portable --specpath build --paths src --add-data "$projectRoot/src/academic_os/web;academic_os/web" --add-data "$projectRoot/src/academic_os/vendor/provenance.json;academic_os/vendor" --add-data "$projectRoot/build/oauth-client.json;academic_os" --collect-all tzdata --collect-submodules uvicorn --collect-submodules keyring.backends --copy-metadata mcp --copy-metadata keyring --hidden-import academic_os.mcp --hidden-import academic_os.moodle_process --hidden-import win32timezone --exclude-module pytest --exclude-module playwright --exclude-module ruff scripts/portable_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Portable application build failed' }
} elseif (-not (Test-Path -LiteralPath (Join-Path $applicationOutput 'AcademicOS.exe'))) { throw 'No existing executable for SkipBuild' }
$licenseRoot = Join-Path $OutputRoot 'AcademicOS/licenses'
New-Item -ItemType Directory -Path $licenseRoot -Force | Out-Null
$libraryRoot = (& $Python -c "import json,sysconfig; print(json.dumps(sysconfig.get_path('purelib')))") | ConvertFrom-Json
Get-ChildItem -LiteralPath $libraryRoot -Directory -Filter '*.dist-info' | ForEach-Object {
    $distribution = $_
    Get-ChildItem -LiteralPath $distribution.FullName -Recurse -File | Where-Object { $_.Name -match '^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)' -or $_.FullName -match '[\\/]licenses[\\/]' } | ForEach-Object {
        $relative = $_.FullName.Substring($distribution.FullName.Length + 1)
        $destination = Join-Path (Join-Path $licenseRoot $distribution.Name) $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}
$pythonRoot = (& $Python -c 'import json,sys; print(json.dumps(sys.base_prefix))') | ConvertFrom-Json
Get-ChildItem -LiteralPath $pythonRoot -File -Filter 'LICENSE*' | Copy-Item -Destination $licenseRoot
Copy-Item -LiteralPath 'THIRD-PARTY-NOTICES.md' -Destination $licenseRoot
Write-Host "Built: $OutputRoot/AcademicOS/AcademicOS.exe"
