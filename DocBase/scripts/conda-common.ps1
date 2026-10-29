function Get-DocbaseCondaCommand {
    # Prefer the base Python module: this also works if the Windows conda.exe
    # launcher cannot start because of a DLL relocation error.
    $candidatePaths = @($env:CONDA_PYTHON_EXE, $env:CONDA_EXE)
    foreach ($name in @('conda.exe', 'conda', 'python.exe', 'python')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found -and $found.Source) { $candidatePaths += $found.Source }
    }
    foreach ($candidate in $candidatePaths) {
        if (-not $candidate) { continue }
        $location = Split-Path -Parent $candidate
        for ($level = 0; $level -lt 4 -and $location; $level++) {
            $basePython = Join-Path $location 'python.exe'
            $condaModule = Join-Path $location 'Lib\site-packages\conda\__main__.py'
            if ((Test-Path -LiteralPath $basePython) -and (Test-Path -LiteralPath $condaModule)) {
                return @{ Executable = $basePython; Arguments = @('-m', 'conda') }
            }
            $location = Split-Path -Parent $location
        }
    }
    $command = Get-Command conda -ErrorAction SilentlyContinue
    if ($command) { return @{ Executable = $command.Name; Arguments = @() } }
    throw 'Conda was not found. Open Anaconda PowerShell Prompt and run this script again.'
}

function Get-DocbaseCondaPrefix {
    param([string]$ProjectRoot)
    if ($env:DOCBASE_CONDA_PREFIX) { return [IO.Path]::GetFullPath($env:DOCBASE_CONDA_PREFIX) }
    return Join-Path $ProjectRoot '.conda'
}
