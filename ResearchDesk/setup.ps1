$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'DocAgent\scripts\conda-common.ps1')

$node = Get-Command node.exe -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
    throw 'Install Node.js 22.12+ with npm, then open a new terminal.'
}
$nodeVersion = [version](& $node.Source -p 'process.versions.node')
if ($nodeVersion -lt [version]'22.12.0') {
    throw "Node.js 22.12+ is required. Detected $nodeVersion."
}

$conda = Get-DocagentCondaCommand
$condaExecutable = $conda.Executable
$condaArguments = $conda.Arguments
$env:PYTHONUTF8 = '1'

function Install-Project([string]$directory, [string]$module) {
    $prefix = Join-Path $directory '.conda'
    $python = Join-Path $prefix 'python.exe'
    Write-Host "Setting up $module"
    if (-not (Test-Path -LiteralPath (Join-Path $prefix 'conda-meta'))) {
        & $condaExecutable @condaArguments create --prefix $prefix python=3.11 pip -y
        if ($LASTEXITCODE -ne 0) { throw "Conda environment creation failed for $module." }
    }
    if (-not (Test-Path -LiteralPath $python)) { throw "Python is missing from $prefix." }

    Push-Location -LiteralPath $directory
    try {
        & $python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed for $module." }
        if ((Test-Path -LiteralPath '.env.example') -and -not (Test-Path -LiteralPath '.env')) {
            Copy-Item -LiteralPath '.env.example' -Destination '.env'
        }
        if ($module -ne 'researchdesk') {
            & $python -m $module doctor
            if ($LASTEXITCODE -ne 0) { throw "Local dependency check failed for $module." }
        }
    } finally { Pop-Location }
}

Install-Project (Join-Path $root 'doc2md') 'doc2md'
Install-Project (Join-Path $root 'DocBase') 'docbase'
Install-Project (Join-Path $root 'DocAgent') 'docagent'
$service = Join-Path $PSScriptRoot 'service'
Install-Project $service 'researchdesk'

Push-Location -LiteralPath (Join-Path $PSScriptRoot 'web')
try {
    $cmd = Join-Path $env:SystemRoot 'System32\cmd.exe'
    & $npm.Source ci --script-shell $cmd
    if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
    & $npm.Source run build --script-shell $cmd
    if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
} finally { Pop-Location }

& (Join-Path $service '.conda\python.exe') -m researchdesk init
if ($LASTEXITCODE -ne 0) { throw 'Service initialization failed.' }
Write-Host 'Setup complete. Run start.cmd and open http://127.0.0.1:8765'
