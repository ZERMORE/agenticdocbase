[CmdletBinding()]
param([string]$Prefix)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\conda-common.ps1')
$condaCommand = Get-DocagentCondaCommand
$condaArguments = $condaCommand.Arguments
if (-not $Prefix) { $Prefix = Get-DocagentCondaPrefix $PSScriptRoot }
$Prefix = [IO.Path]::GetFullPath($Prefix)
Push-Location $PSScriptRoot
try {
    if (Test-Path -LiteralPath (Join-Path $Prefix 'conda-meta')) {
        & ($condaCommand.Executable) @condaArguments env update --prefix $Prefix --file environment.yml
    } else {
        & ($condaCommand.Executable) @condaArguments env create --prefix $Prefix --file environment.yml
    }
    if ($LASTEXITCODE -ne 0) { throw 'Conda environment setup failed. Check the output above.' }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    & ($condaCommand.Executable) @condaArguments run --no-capture-output --prefix $Prefix python -m docagent doctor
    $setupExit = $LASTEXITCODE
} finally { Pop-Location }
exit $setupExit
