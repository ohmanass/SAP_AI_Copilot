@echo off
rem SAP AI Copilot: double-click to start the application locally (see start.ps1).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
