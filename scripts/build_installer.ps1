<#
.SYNOPSIS
    Build the Manuphet Windows installer.

.DESCRIPTION
    1. Build Manuphet_Web.exe with PyInstaller
    2. Build Manuphet_Setup_Wizard.exe with PyInstaller
    3. Build Manuphet_Setup_x.x.x.exe with Inno Setup (ISCC)

.NOTES
    Prerequisites:
      pip install pyinstaller
      Inno Setup 6
#>

param(
    [string]$Version        = "",
    [string]$ProjectDir     = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$InnoSetupExe   = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [string]$PyInstallerExe = "pyinstaller"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Version) {
    $Version = (Get-Content (Join-Path $ProjectDir "version.txt") -Raw).Trim()
}
Write-Host "Version: $Version" -ForegroundColor Cyan

if (-not (Test-Path $InnoSetupExe)) {
    throw "Inno Setup not found: $InnoSetupExe"
}

Push-Location $ProjectDir
try {
    Write-Host "`n=== [1/3] Building Manuphet_Web.exe ===" -ForegroundColor Cyan
    & $PyInstallerExe installer\run_web.spec --distpath installer\dist --workpath installer\build --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "Manuphet_Web.exe build failed." }

    Write-Host "`n=== [2/3] Building Manuphet_Setup_Wizard.exe ===" -ForegroundColor Cyan
    & $PyInstallerExe installer\setup_wizard.spec --distpath installer\dist --workpath installer\build --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "Manuphet_Setup_Wizard.exe build failed." }

    Write-Host "`n=== [3/3] Building installer ===" -ForegroundColor Cyan
    & $InnoSetupExe "/DAppVersion=$Version" installer\manuphet.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

    Write-Host "`n=== Done ===" -ForegroundColor Green
    Get-ChildItem installer\Output\*.exe | Select-Object Name, Length, LastWriteTime
} finally {
    Pop-Location
}
