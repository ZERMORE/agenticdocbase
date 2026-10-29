$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\conda-common.ps1')
$condaCommand = Get-DocbaseCondaCommand
$condaArguments = $condaCommand.Arguments
$projectPrefix = Get-DocbaseCondaPrefix $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectPrefix 'conda-meta'))) {
    Write-Error 'Run setup.ps1 first.'
    exit 1
}
# Resolve user arguments relative to the caller, not the project directory.
& ($condaCommand.Executable) @condaArguments run --no-capture-output --prefix $projectPrefix python -m docbase @args
exit $LASTEXITCODE
