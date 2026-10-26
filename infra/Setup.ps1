[CmdletBinding()]
param([switch]$Dev, [switch]$Offline)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    if (!(Test-Path .venv\Scripts\python.exe)) {
        python -c "import sys; assert sys.version_info[:2] == (3,13)"
        if ($LASTEXITCODE) { throw 'CPython 3.13 required' }
        python -m venv .venv
        if ($LASTEXITCODE) { throw 'venv creation failed' }
    }
    $py = Join-Path $root '.venv\Scripts\python.exe'
    & $py -c "import sys; assert sys.version_info[:2] == (3,13) and sys.prefix != sys.base_prefix"
    if ($LASTEXITCODE) { throw 'isolated CPython 3.13 required' }
    $manifest = 'requirements.txt'
    if ($Dev) { $manifest = 'requirements-dev.txt' }
    $extra = @()
    if ($Offline) { $extra = @('--no-index', '--find-links', 'infra/wheels') }
    & $py -m pip --disable-pip-version-check install --require-hashes --only-binary=:all: @extra -r $manifest
    if ($LASTEXITCODE) { throw 'pinned installation failed' }
    & $py -m pip check
    if ($LASTEXITCODE) { throw 'dependency check failed' }
    & $py -c "import fastapi,pydantic,uvicorn,argon2,tzdata; print('runtime imports ready')"
    if ($LASTEXITCODE) { throw 'runtime import check failed' }
    & $py -c "from zoneinfo import ZoneInfo; from datetime import datetime; z=ZoneInfo('America/New_York'); assert datetime(2026,1,15,tzinfo=z).utcoffset().total_seconds()==-18000; assert datetime(2026,7,15,tzinfo=z).utcoffset().total_seconds()==-14400; print('timezone ready')"
    if ($LASTEXITCODE) { throw 'timezone check failed' }
} finally { Pop-Location }
