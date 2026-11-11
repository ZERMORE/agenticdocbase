@echo off
setlocal
set PYTHONUTF8=1
if not exist "%~dp0service\.conda\python.exe" (
  echo ResearchDesk is not installed. Run setup.cmd first.
  exit /b 1
)
"%~dp0service\.conda\python.exe" -X utf8 -m researchdesk start
exit /b %errorlevel%
