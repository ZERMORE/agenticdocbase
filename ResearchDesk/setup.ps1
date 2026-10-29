$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\DocAgent\scripts\conda-common.ps1')
$command = Get-DocagentCondaCommand
$arguments = $command.Arguments
$service = Join-Path $PSScriptRoot 'service'
$prefix = Join-Path $service '.conda'
Push-Location $service
try {
    if (-not (Test-Path -LiteralPath (Join-Path $prefix 'conda-meta'))) {
        & ($command.Executable) @arguments create --prefix $prefix python=3.11 pip -y
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create independent Conda environment.' }
    }
    & (Join-Path $prefix 'python.exe') -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Service dependency installation failed.' }
    & (Join-Path $prefix 'python.exe') -m researchdesk init
    if ($LASTEXITCODE -ne 0) { throw 'Service initialization failed.' }
} finally { Pop-Location }
Push-Location (Join-Path $PSScriptRoot 'web')
try {
    if (-not $env:ComSpec) { $env:ComSpec = Join-Path $env:SystemRoot 'System32\cmd.exe' }
    & npm.cmd ci --script-shell $env:ComSpec
    if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
    & npm.cmd run build --script-shell $env:ComSpec
    if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
} finally { Pop-Location }
Write-Host 'Setup complete. Run start.cmd and open http://127.0.0.1:8765'
