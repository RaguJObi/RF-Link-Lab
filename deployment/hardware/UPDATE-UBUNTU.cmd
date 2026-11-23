@echo off
setlocal
title RF Link Lab - update existing Ubuntu installation
if not exist "%~dp0SHA256SUMS" (
  echo Run this file from the complete release transfer kit.
  pause
  exit /b 2
)
set "RFLL_TARGET=rfll@ubuntu-bench.local"
set "RFLL_PROJECT=/home/rfll/SDR/bladeRF/RF-Link-Lab"
set "RFLL_KIT=/home/rfll/SDR/bladeRF/RF-Link-Lab-update-%RANDOM%-%RANDOM%"
echo This copies and updates the existing Ubuntu project.
echo It does not flash either board or start RF.
echo Enter the Ubuntu password only at the SCP/SSH prompts.
scp -r -o ConnectTimeout=10 -o StrictHostKeyChecking=yes "%~dp0." "%RFLL_TARGET%:%RFLL_KIT%"
if errorlevel 1 (
  echo Transfer did not finish. Existing project files were not updated.
  pause
  exit /b 3
)
ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=yes "%RFLL_TARGET%" "bash '%RFLL_KIT%/UPDATE-RF-LINK-LAB.sh' --project '%RFLL_PROJECT%'"
if errorlevel 1 (
  echo Update stopped. Read the message above before retrying.
  pause
  exit /b 4
)
echo Update complete. Follow READY-FOR-HARDWARE.md for the separate connected-bench run.
pause
