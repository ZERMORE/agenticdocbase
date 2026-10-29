@echo off
setlocal
set PYTHONUTF8=1
"%~dp0service\.conda\python.exe" -X utf8 -m researchdesk start
exit /b %errorlevel%
