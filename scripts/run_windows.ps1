$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    throw 'Run SETUP_WINDOWS.cmd first.'
}
$env:Path = @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
              [Environment]::GetEnvironmentVariable('Path', 'User'), $env:Path) -join ';'
& '.\.venv\Scripts\python.exe' scripts/check_environment.py
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed. See the error above.' }
& '.\.venv\Scripts\python.exe' app.py @args
exit $LASTEXITCODE
