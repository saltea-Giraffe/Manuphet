<#
.SYNOPSIS  Remove the Manuphet Web auto-start task.
.NOTES     Requires administrator privileges.
#>

param(
    [string]$TaskName = "Manuphet_Web"
)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' is not registered."
    exit 0
}

if ($existing.State -eq "Running") {
    Write-Host "Stopping task..."
    Stop-ScheduledTask -TaskName $TaskName
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "[OK] Task '$TaskName' removed."
