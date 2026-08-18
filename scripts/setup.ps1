$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $pythonCommand) {
    Write-Error "Python 3.11 or newer is required."
    exit 1
}

& $pythonCommand.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python 3.11 or newer is required."
    exit 1
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
& $pythonCommand.Source -m codex_configurator setup @args
exit $LASTEXITCODE
