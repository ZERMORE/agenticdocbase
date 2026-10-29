@echo off
setlocal
rem Explicitly invoke PowerShell; do not rely on the .ps1 file association.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %errorlevel%
