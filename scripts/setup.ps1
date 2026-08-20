& {
param(
    [bool]$scriptIsFileInvocation,
    [object[]]$forwardedArgs
)

# Keep preferences and working variables out of the caller's scope when this
# script is evaluated instead of run with `-File`.
$ErrorActionPreference = "Stop"

$projectRoot = if ($scriptIsFileInvocation) {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    (Get-Location).Path
}
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
    Write-Warning "Python 3.11 or newer is required."
    if ($scriptIsFileInvocation) {
        exit 1
    }
    $global:LASTEXITCODE = 1
    return
}

$sourcePath = Join-Path $projectRoot "src"
$priorPythonPath = $env:PYTHONPATH
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$sourcePath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $sourcePath
}
$command = $selected.Command
$prefix = @($selected.Prefix)
try {
    & $command @prefix -m codex_configurator setup @forwardedArgs
    $exitCode = $LASTEXITCODE
} catch {
    Write-Warning ("Codex setup failed: " + $_.Exception.Message)
    $exitCode = 1
} finally {
    if ($null -eq $priorPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $priorPythonPath
    }
}
if ($scriptIsFileInvocation) {
    # A transient `powershell -File` window may close after the success/error
    # status has already been printed by the configurator.
    exit $exitCode
}

# The fixed one-line command runs in the user's existing host. Returning here
# keeps that host alive while still exposing the child status to callers.
$global:LASTEXITCODE = $exitCode
return
} (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) @args
