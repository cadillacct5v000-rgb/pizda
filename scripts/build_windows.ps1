param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not $Python) { $Python = Join-Path (Get-Location) '.venv\Scripts\python.exe' }
& $Python scripts/check_environment.py
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
& $Python -m pytest -q --junitxml=test-results/windows.xml
if ($LASTEXITCODE -ne 0) { throw 'Tests failed. The build has been stopped.' }
& $Python -m PyInstaller --noconfirm KadroGen.spec 2>&1 | Tee-Object -FilePath test-results/build.log
if ($LASTEXITCODE -ne 0) {
    $details = ((Get-Content test-results/build.log -Tail 35) -join "`n").Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
    Write-Output "::error title=PyInstaller::$details"
    throw 'PyInstaller failed.'
}
Copy-Item KadroGen.exe.config dist/KadroGen/KadroGen.exe.config -Force
Copy-Item WINDOWS.md dist/KadroGen/WINDOWS.md -Force
Copy-Item docs/UNIFIED_WINDOWS_RU.txt dist/KadroGen/READ_ME_FIRST_RU.txt -Force
foreach ($relative in @('KadroGen.exe', '_internal/ffmpeg/ffmpeg.exe', '_internal/ffmpeg/ffprobe.exe', '_internal/runtime/deno.exe')) {
    if (-not (Test-Path (Join-Path 'dist/KadroGen' $relative))) { throw "Build output is missing $relative" }
}
& '.\dist\KadroGen\_internal\ffmpeg\ffmpeg.exe' -version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) { throw 'Bundled FFmpeg failed.' }
& '.\dist\KadroGen\_internal\runtime\deno.exe' --version
if ($LASTEXITCODE -ne 0) { throw 'Bundled Deno failed.' }
$process = Start-Process -FilePath (Join-Path (Get-Location) 'dist/KadroGen/KadroGen.exe') -ArgumentList '--help' -PassThru
if (-not $process.WaitForExit(60000)) {
    $process.Kill()
    throw 'Bundled executable --help timed out.'
}
if ($process.ExitCode -ne 0) { throw "Bundled executable failed (exit $($process.ExitCode))." }
$Report = Join-Path (Get-Location) 'test-results/bundled-unified.json'
$process = Start-Process -FilePath (Join-Path (Get-Location) 'dist/KadroGen/KadroGen.exe') -ArgumentList @('--self-test-unified', ('"' + $Report + '"')) -PassThru
if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'Bundled unified self-test timed out.' }
if ($process.ExitCode -ne 0 -or -not (Test-Path $Report)) { throw 'Bundled unified self-test failed.' }
$Result = Get-Content -Raw $Report | ConvertFrom-Json
if (-not $Result.ok -or $Result.version -ne '1.9.1-dev') { throw 'Bundled unified self-test returned invalid version.' }
Compress-Archive -Path dist/KadroGen -DestinationPath dist/KadroGen-Windows-x64.zip -Force
Write-Host 'Build complete: dist/KadroGen-Windows-x64.zip'
