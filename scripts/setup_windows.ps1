# Windows 10/11 x64. Run with your normal user account; installers may ask for elevation.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

function Refresh-ToolPath {
    $env:Path = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path,
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
    ) -join ';'
}

function Install-Package([string]$Id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'WinGet is missing. Install/update Microsoft App Installer from Microsoft Store, then run SETUP_WINDOWS.cmd again.'
    }
    & winget install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "WinGet could not install $Id (exit $LASTEXITCODE)." }
    Refresh-ToolPath
}

function Get-Python312Path([string]$Command, [string[]]$PythonArgs = @()) {
    try {
        # PowerShell 5.1 and Python may use different encodings for native stdout.
        # ASCII JSON preserves Unicode paths without changing console settings.
        $encoded = & $Command @PythonArgs -c 'import json, sys; print(json.dumps(sys.executable)); sys.exit(sys.version_info[:2] != (3, 12))' 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $encoded) { return $null }
        $found = ConvertFrom-Json -InputObject ($encoded -join "`n")
        if ($found -is [string] -and (Test-Path -LiteralPath $found -PathType Leaf)) {
            return $found
        }
    } catch { }
    return $null
}

function Find-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = Get-Python312Path -Command 'py' -PythonArgs @('-3.12')
        if ($found) { return $found }
    }
    foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", 'python')) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch '\\WindowsApps\\') {
            $found = Get-Python312Path -Command $candidate
            if ($found) { return $found }
        }
    }
    return $null
}

Refresh-ToolPath
$python = Find-Python312
if (-not $python) {
    Install-Package 'Python.Python.3.12'
    $python = Find-Python312
}
if (-not $python) { throw 'Python 3.12 could not be found. Close this window and run SETUP_WINDOWS.cmd again.' }

if (-not ((Test-Path 'ffmpeg\ffmpeg.exe') -and (Test-Path 'ffmpeg\ffprobe.exe')) -and
    -not ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue))) {
    Install-Package 'Gyan.FFmpeg'
}
if (-not (Test-Path 'runtime\deno.exe') -and -not (Get-Command deno -ErrorAction SilentlyContinue)) {
    Install-Package 'DenoLand.Deno'
}

$webViewId = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$webViewReady = $false
foreach ($key in @("HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$webViewId",
                   "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$webViewId",
                   "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$webViewId")) {
    $version = Get-ItemPropertyValue -Path $key -Name pv -ErrorAction SilentlyContinue
    if ($version -and $version -ne '0.0.0.0') { $webViewReady = $true }
}
if (-not $webViewReady) { Install-Package 'Microsoft.EdgeWebView2Runtime' }

& $python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. See the error above.' }
& '.\.venv\Scripts\python.exe' scripts/check_environment.py
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed. See the error above.' }
Write-Host 'Setup complete. Open START_WINDOWS.cmd to launch Kadro.Gen.'
