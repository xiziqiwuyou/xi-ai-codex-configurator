$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$candidates = @()
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $candidates += [pscustomobject]@{
        Command = $pyLauncher.Source
        Prefix = @("-3")
    }
}
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $candidates += [pscustomobject]@{
        Command = $pythonCommand.Source
        Prefix = @()
    }
}

$selected = $null
foreach ($candidate in $candidates) {
    $command = $candidate.Command
    $prefix = @($candidate.Prefix)
    & $command @prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -eq 0) {
        $selected = $candidate
        break
    }
}
if (-not $selected) {
    Write-Error "Python 3.11 or newer is required."
    exit 1
}

$sourcePath = Join-Path $projectRoot "src"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$sourcePath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $sourcePath
}
$command = $selected.Command
$prefix = @($selected.Prefix)
& $command @prefix -m codex_configurator setup @args
exit $LASTEXITCODE
