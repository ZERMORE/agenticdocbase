$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\conda-common.ps1')
$condaCommand = Get-Doc2mdCondaCommand
$condaArguments = $condaCommand.Arguments
$projectPrefix = Get-Doc2mdCondaPrefix $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectPrefix 'conda-meta'))) {
    Write-Error 'Run setup.ps1 first.'
    exit 1
}
# Resolve user arguments relative to the caller, not the project directory.
& ($condaCommand.Executable) @condaArguments run --no-capture-output --prefix $projectPrefix python -m doc2md @args
exit $LASTEXITCODE
