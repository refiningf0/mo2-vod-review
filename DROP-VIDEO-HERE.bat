@echo off
setlocal enabledelayedexpansion
title MO2 Fight Log

cd /d "%~dp0"

if "%~1"=="" (
  echo.
  echo   Drag a video file onto this .bat and let go.
  echo.
  pause
  exit /b
)

rem This machine has more than one Python, and Explorer does not resolve
rem "python" the same way a terminal does -- which is why the tool ran by hand
rem and failed on a drag-and-drop with "No module named PIL". Pick by what can
rem actually import what we need, not by whatever "python" happens to mean.
set "PY="
call :pick "py -3.14"
call :pick "py -3"
call :pick "python"
call :pick "C:\Python314\python.exe"
call :pick "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY goto nopython

set "VIDEO=%~1"
set "NAME=%~n1"
set "JSON=%NAME%.json"
set "HTML=%NAME%.html"

echo.
echo   Reading: %~nx1
echo   About 90 seconds per 2 minutes of footage.
echo.

%PY% mo2log.py "%VIDEO%" --fps 2 --out "%JSON%"
if errorlevel 1 goto failed

%PY% make_report.py "%JSON%" "%HTML%"
if errorlevel 1 goto failed

echo.
echo   Done. Opening %HTML%
start "" "%HTML%"
exit /b

:pick
if defined PY exit /b
%~1 -c "import PIL, numpy" >nul 2>&1 || exit /b
set "PY=%~1"
exit /b

:nopython
echo.
echo   No Python here could import both Pillow and numpy.
echo   Each candidate was tried; this is what they said:
echo.
call :show "py -3.14"
call :show "py -3"
call :show "python"
call :show "C:\Python314\python.exe"
call :show "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
echo   Usually fixed by:  py -m pip install pillow numpy
echo.
pause
exit /b

:show
echo   ---^> %~1
%~1 -c "import PIL, numpy; print('    ok')" 2>&1
echo.
exit /b

:failed
echo.
echo   Something went wrong. The messages above say what.
echo.
echo   Most common cause: the combat log sits somewhere other than the
echo   bottom-left of the frame, so nothing was found. Run it by hand with
echo   your own crop:
echo.
echo     %PY% mo2log.py "%VIDEO%" --crop 0,756,998,280
echo.
pause
