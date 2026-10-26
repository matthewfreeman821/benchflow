$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (!(Test-Path $Python)) { throw 'Run infra/Setup.ps1 first' }
function Invoke-BenchFlow([string[]]$Arguments) {
    Push-Location $ProjectRoot
    try {
        & $Python -m infra.operations @Arguments
        if ($LASTEXITCODE) { throw "BenchFlow operation failed (exit $LASTEXITCODE); no secret diagnostics emitted" }
    } finally { Pop-Location }
}
