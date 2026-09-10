$ErrorActionPreference = 'Stop'
$LocalPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$Launcher = Join-Path $PSScriptRoot 'run.py'
if (Test-Path $LocalPython) {
    & $LocalPython $Launcher @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Launcher @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $Launcher @args
} else {
    throw 'Python 3.10+ is required. Install Python, then run this launcher again.'
}
exit $LASTEXITCODE
