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

rem One entry point, the same one the packaged .exe runs. This used to repeat
rem the two steps itself and name the output files here, which is how the two
rem of them drifted apart: this wrote them into the tool's folder, the .exe
rem wrote them beside the clip. mo2fightlog.py decides that now, once.
%PY% mo2fightlog.py "%VIDEO%"
if errorlevel 1 goto failed
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
rem mo2fightlog.py has already said what went wrong and waited to be read,
rem so this only adds the one thing it cannot know: how to re-run by hand
rem with a crop of your own, using the Python that was actually found here.
echo.
echo   If the log sits somewhere other than the bottom-left of the frame,
echo   nothing will be found. Run it by hand with your own crop:
echo.
echo     %PY% mo2log.py "%VIDEO%" --crop 0,756,998,280
echo.
pause
