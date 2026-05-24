@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title Solaris Asset Manager Installer

echo.
echo  ============================================================
echo   Solaris Asset Manager  v1.0.0  --  Windows Installer
echo  ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "INSTALLER=%SCRIPT_DIR%installer.py"

REM --- 1. python in PATH -------------------------------------------------------
where python >nul 2>&1
if %errorlevel% equ 0 (
    echo  Found: Python
    python "%INSTALLER%"
    goto :done
)

REM --- 2. py launcher ----------------------------------------------------------
where py >nul 2>&1
if %errorlevel% equ 0 (
    echo  Found: Python Launcher
    py -3 "%INSTALLER%"
    goto :done
)

REM --- 3. hython in PATH (Houdini shell) ---------------------------------------
where hython >nul 2>&1
if %errorlevel% equ 0 (
    echo  Found: hython in PATH
    hython "%INSTALLER%"
    goto :done
)

REM --- 4. Common Houdini install locations -------------------------------------
for %%V in (21.5 21.0 20.5 20.0 19.5 19.0) do (
    for %%D in (
        "C:\Program Files\Side Effects Software\Houdini %%V\bin\hython.exe"
        "C:\Program Files\Side Effects Software\Houdini %%V\bin\hython3.exe"
    ) do (
        if exist %%D (
            echo  Found: Houdini %%V
            %%D "%INSTALLER%"
            goto :done
        )
    )
)

REM --- Not found ---------------------------------------------------------------
echo  ERROR: Python 3 not found.
echo.
echo  Options:
echo    A) Install Python from https://python.org and re-run this script.
echo    B) Open a Houdini shell (Start Menu ^> Houdini ^> Command Line Tools)
echo       and run:  python install\installer.py
echo.

:done
echo.
pause
