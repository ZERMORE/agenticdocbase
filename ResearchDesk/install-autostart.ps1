# Optional: run explicitly to start Research Desk when this Windows user logs on.
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$python = Join-Path $root 'service\.conda\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.cmd first.' }
$action = New-ScheduledTaskAction -Execute $python -Argument '-X utf8 -m researchdesk start' -WorkingDirectory (Join-Path $root 'service')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName 'ResearchDesk-Local' -Action $action -Trigger $trigger -Settings $settings -Description 'Start local Research Desk at user logon' -Force
