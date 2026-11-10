param([ValidateSet('Install','Start','Stop','Check','Monitor','Update')][string]$Action = 'Start', [string]$Archive, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$App = Join-Path $Root 'app'
$Data = Join-Path $Root 'data'
$Runtime = Join-Path $Root 'runtime'
$Logs = Join-Path $Root 'logs'
$Config = Join-Path $Root 'config'
$PidFile = Join-Path $Root 'backend.pid'
foreach ($Folder in @($Data,$Runtime,$Logs,$Config)) { New-Item -ItemType Directory -Force -Path $Folder | Out-Null }
$env:DATA_DIR = $Data
$env:VIDEO_FLOW_CONFIG_DIR = $Config
$env:ONNXRUNTIME_NODE_INSTALL_CUDA = 'skip'
$env:PORT = '8787'
$Base = 'http://127.0.0.1:8787'

function Manifest($Directory) {
    $ManifestPath = Join-Path $Directory 'package-manifest.json'
    if (!(Test-Path -LiteralPath $ManifestPath)) { throw 'Package manifest is missing.' }
    return Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
}
function Verify-Package($Directory) {
    $Description = Manifest $Directory
    $Prefix = [IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\'
    foreach ($Entry in $Description.files.PSObject.Properties) {
        $File = [IO.Path]::GetFullPath((Join-Path $Directory $Entry.Name))
        if (!$File.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe package path.' }
        if (!(Test-Path -LiteralPath $File -PathType Leaf)) { throw "Package file missing: $($Entry.Name)" }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLowerInvariant() -ne $Entry.Value) { throw "Checksum mismatch: $($Entry.Name)" }
    }
    return $Description
}
function Node-Path {
    $Candidate = Get-ChildItem -LiteralPath $Runtime -Filter node.exe -Recurse | Select-Object -First 1
    if (!$Candidate) { throw 'Run Install Video AI Auto.bat first.' }
    return $Candidate.FullName
}
function Own-Backend($BackendId) {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $BackendId" -ErrorAction SilentlyContinue
    return $Process -and $Process.CommandLine -and $Process.CommandLine.Contains((Join-Path $App 'server\index.mjs'))
}
function Health { try { return Invoke-RestMethod "$Base/api/health" -TimeoutSec 2 } catch { return $null } }
function Stop-Backend {
    $Current = Health
    if (!$Current) { if (Test-Path $PidFile) { Remove-Item -LiteralPath $PidFile -Force }; return }
    if (!(Own-Backend $Current.pid)) { throw 'Port 8787 belongs to another program or installation. It will not be stopped.' }
    $State = Invoke-RestMethod "$Base/api/state" -TimeoutSec 5
    Invoke-RestMethod "$Base/api/shutdown" -Method Post -Headers @{'X-CSRF-Token'=$State.csrf} -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    for ($Attempt=0; $Attempt -lt 30; $Attempt++) { if (!(Get-Process -Id $Current.pid -ErrorAction SilentlyContinue)) { break }; Start-Sleep -Seconds 1 }
    if (Get-Process -Id $Current.pid -ErrorAction SilentlyContinue) { throw 'Backend is still shutting down. Wait and run Stop again; no process was killed.' }
    if (Test-Path $PidFile) { Remove-Item -LiteralPath $PidFile -Force }
}
function Start-Backend {
    $Current = Health
    if ($Current) {
        if (!(Own-Backend $Current.pid)) { throw 'Another program or installation is using port 8787.' }
        if (!$NoBrowser) { Start-Process $Base }; return
    }
    $Node = Node-Path
    $Process = Start-Process -FilePath $Node -ArgumentList @(('"' + (Join-Path $App 'server\index.mjs') + '"')) -WorkingDirectory $App -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Logs 'stdout.log') -RedirectStandardError (Join-Path $Logs 'stderr.log') -PassThru
    Set-Content -LiteralPath $PidFile -Value $Process.Id -Encoding ascii
    for ($Attempt=0; $Attempt -lt 60; $Attempt++) {
        $Current = Health
        if ($Current -and $Current.pid -eq $Process.Id) { if (!$NoBrowser) { Start-Process $Base }; return }
        if ($Process.HasExited) { throw 'Backend exited. Check logs\stderr.log.' }
        Start-Sleep -Milliseconds 500
    }
    throw 'Backend did not become ready. Check logs\stderr.log.'
}
function Install-App {
    if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This package requires Windows x64.' }
    $Description = Verify-Package $Root
    if (!(Test-Path -LiteralPath (Join-Path $Runtime ("node-" + $Description.nodeVersion + '-win-x64\node.exe')))) { Expand-Archive -LiteralPath (Join-Path $Root $Description.nodeArchive) -DestinationPath $Runtime -Force }
    $Node = Node-Path
    $Npm = Join-Path (Split-Path $Node -Parent) 'npm.cmd'
    $env:PATH = (Split-Path $Node -Parent) + ';' + $env:PATH
    Push-Location $App
    try {
        & $Npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        & $Npm run check
        if ($LASTEXITCODE -ne 0) { throw 'Quality gate failed.' }
        & $Node (Join-Path $App 'node_modules\@remotion\cli\remotion-cli.js') browser ensure
        if ($LASTEXITCODE -ne 0) { throw 'Remotion browser installation failed.' }
    } finally { Pop-Location }
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Video Flow.lnk'))
    $Shortcut.TargetPath = Join-Path $Root 'Start.bat'; $Shortcut.WorkingDirectory = $Root; $Shortcut.Save()
}
function Apply-Update {
    if (!$Archive) { $Archive = Read-Host 'Full path to the new VideoFlow ZIP' }
    $Archive = [IO.Path]::GetFullPath($Archive.Trim('"'))
    if (!(Test-Path -LiteralPath $Archive -PathType Leaf)) { throw 'Update ZIP not found.' }
    $Stage = Join-Path $Root ('update-' + [Guid]::NewGuid().ToString('N'))
    $Backup = Join-Path $Root ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $Stage,$Backup | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        if ($Zip.Entries.Count -gt 10000) { throw 'Update has too many files.' }
        foreach ($Entry in $Zip.Entries) { if ($Entry.FullName.Contains('..') -or [IO.Path]::IsPathRooted($Entry.FullName) -or $Entry.Length -gt 536870912) { throw 'Unsafe update archive.' } }
    } finally { $Zip.Dispose() }
    $OldApp = Join-Path $Root 'app.previous'
    $Stopped = $false
    try {
        Expand-Archive -LiteralPath $Archive -DestinationPath $Stage
        $Description = Verify-Package $Stage
        if ([version]$Description.version -lt [version](Manifest $Root).version) { throw 'Downgrade requires manual restore.' }
        Stop-Backend; $Stopped = $true
        Copy-Item -LiteralPath $Data -Destination $Backup -Recurse
        Copy-Item -LiteralPath $Config -Destination $Backup -Recurse
        if (Test-Path $OldApp) { Remove-Item -LiteralPath $OldApp -Recurse -Force }
        Move-Item -LiteralPath $App -Destination $OldApp
        Move-Item -LiteralPath (Join-Path $Stage 'app') -Destination $App
        # Data/config/runtime are outside app and are never replaced by an update.
        $Node = Node-Path; $Npm = Join-Path (Split-Path $Node -Parent) 'npm.cmd'; $env:PATH = (Split-Path $Node -Parent) + ';' + $env:PATH
        Push-Location $App
        try { & $Npm ci; if ($LASTEXITCODE -ne 0) { throw 'Update npm ci failed.' }; & $Npm run check; if ($LASTEXITCODE -ne 0) { throw 'Update checks failed.' } } finally { Pop-Location }
        Start-Backend
        Copy-Item -LiteralPath (Join-Path $Stage 'package-manifest.json') -Destination $Root -Force
        # Keep launchers stable; future launcher migrations require a versioned installer.
        Write-Host 'Update ready. Previous app and data/config backups retained.'
    } catch {
        $Failure = $_
        if ($Stopped -and (Test-Path $OldApp)) {
            Stop-Backend
            if (Test-Path $App) { Remove-Item -LiteralPath $App -Recurse -Force }
            Move-Item -LiteralPath $OldApp -Destination $App
            Remove-Item -LiteralPath $Data,$Config -Recurse -Force
            Copy-Item -LiteralPath (Join-Path $Backup 'data'),(Join-Path $Backup 'config') -Destination $Root -Recurse
            Start-Backend
        }
        throw $Failure
    } finally { if (Test-Path $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force } }
}
try {
    switch ($Action) {
        'Install' { Start-Transcript -Path (Join-Path $Logs 'install.log') -Append; try { Install-App; Start-Backend } finally { Stop-Transcript } }
        'Start' { Start-Backend }
        'Stop' { Stop-Backend }
        'Check' { $Current = Health; if (!$Current) { throw 'Backend is not running.' }; $Current | ConvertTo-Json -Depth 8 }
        'Monitor' { Start-Backend; if (!$NoBrowser) { Start-Process "$Base/backend-monitor" } }
        'Update' { Apply-Update }
    }
} catch { Write-Host $_ -ForegroundColor Red; exit 1 }
