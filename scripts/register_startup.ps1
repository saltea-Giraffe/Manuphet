<#
.SYNOPSIS  Register Manuphet Web in Task Scheduler via XML (run at startup, SYSTEM, no window).
.NOTES     Requires administrator privileges.
#>

param(
    [string]$InstallDir = "",
    [string]$PythonExe  = "",
    [int]   $Port       = 8000,
    [string]$TaskName   = "Manuphet_Web",
    [string]$LogDir     = ""
)

if (-not $InstallDir) {
    $InstallDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if (-not $LogDir) {
    $LogDir = Join-Path $env:ProgramData "Manuphet\data\logs"
}

# --- Admin check ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run as administrator."
    exit 1
}

# --- Resolve executable ---
function Resolve-Exe {
    param([string]$Hint, [string]$Dir)
    if ($Hint -match "python\.exe$") {
        $pw = $Hint -replace "python\.exe$", "pythonw.exe"
        if (Test-Path $pw) { return $pw }
    }
    if ($Hint -and (Test-Path $Hint)) { return $Hint }
    foreach ($rel in @("Manuphet_Web.exe", ".venv\Scripts\pythonw.exe", ".venv\Scripts\python.exe")) {
        $c = Join-Path $Dir $rel
        if (Test-Path $c) { return $c }
    }
    $found = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return $null
}

$exeToRun = Resolve-Exe -Hint $PythonExe -Dir $InstallDir
if (-not $exeToRun) { Write-Error "Executable not found."; exit 1 }

Write-Host "InstallDir : $InstallDir"
Write-Host "Exe        : $exeToRun"
Write-Host "Port       : $Port"
Write-Host "TaskName   : $TaskName"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# --- Arguments: the bundled exe needs none, Python needs run_web.py ---
$isBundled = ($exeToRun -match "Manuphet_Web\.exe$")
if ($isBundled) {
    $argField = ""
} else {
    $argField = "`"" + (Join-Path $InstallDir "run_web.py") + "`""
}

function Escape-Xml([string]$s) {
    return $s -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;' -replace '"', '&quot;'
}
$xmlExe  = Escape-Xml $exeToRun
$xmlArgs = Escape-Xml $argField
$xmlDir  = Escape-Xml $InstallDir

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Manuphet Web service (auto-start, no window)</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$xmlExe</Command>
      <Arguments>$xmlArgs</Arguments>
      <WorkingDirectory>$xmlDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

try {
    Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force | Out-Null
} catch {
    Write-Error "Register-ScheduledTask failed: $_"
    exit 1
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Error "Task registration could not be verified."
    exit 1
}

# --- Save the executable path and port to settings.json ---
$cfg = Join-Path $env:ProgramData "Manuphet\data\config\settings.json"
if (Test-Path $cfg) {
    try {
        $s = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
        $s | Add-Member -NotePropertyName "python_exe" -NotePropertyValue $exeToRun -Force
        $s | Add-Member -NotePropertyName "port" -NotePropertyValue $Port -Force
        [IO.File]::WriteAllText($cfg, ($s | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    } catch {}
}

Write-Host "OK: Task '$TaskName' registered. Starts 30s after next boot."
Write-Host "    Run now  : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "    Log      : $LogDir\service.log"
