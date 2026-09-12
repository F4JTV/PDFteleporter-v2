@echo off
setlocal enabledelayedexpansion
rem ---------------------------------------------------------------------------
rem  Builds PDF Teleporter and its installer.
rem
rem    build.cmd             build the application only
rem    build.cmd full        build the application, then the installer
rem    build.cmd fresh       rebuild the virtual environment from scratch
rem    build.cmd full fresh  both
rem
rem  A dedicated virtual environment is created under .venv-build and every
rem  step runs inside it. This is not tidiness. PyInstaller bundles whatever it
rem  finds importable in the environment it runs from, so building against a
rem  general-purpose interpreter is how a release ends up carrying packages the
rem  application never imports -- the original shipped 846 MB that way. It also
rem  removes the two-interpreter trap, where dependencies are installed into
rem  one Python and PyInstaller runs under another, producing an executable
rem  that only fails on a clean machine.
rem
rem  Requires: Python 3.10+ on PATH, and for the installer step Inno Setup 7.
rem ---------------------------------------------------------------------------

cd /d "%~dp0"

set "VENV=.venv-build"
set "VPY=%VENV%\Scripts\python.exe"

set "WANT_INSTALLER="
set "WANT_FRESH="
for %%A in (%*) do (
    if /i "%%~A"=="full"  set "WANT_INSTALLER=1"
    if /i "%%~A"=="fresh" set "WANT_FRESH=1"
)

rem ---------------------------------------------------------------- 1/5 ---
echo [1/5] Preparing the build environment...

where python >nul 2>&1 || (echo ERROR: python not found on PATH & exit /b 1)

if defined WANT_FRESH if exist "%VENV%" (
    echo     Removing the previous environment...
    rmdir /s /q "%VENV%"
)

if not exist "%VPY%" (
    echo     Creating %VENV% ...
    python -m venv "%VENV%" || (
        echo ERROR: could not create the virtual environment.
        exit /b 1
    )
    set "NEEDS_INSTALL=1"
)

for /f "delims=" %%V in ('"%VPY%" -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%V"
echo     Interpreter: %VPY%  ^(Python !PYVER!^)

rem Install only when something is actually missing, so a repeat build does
rem not wait on the network for nothing.
"%VPY%" -c "import PyQt6.QtWidgets, pymupdf, PIL, PyInstaller" >nul 2>&1
if errorlevel 1 set "NEEDS_INSTALL=1"

if defined NEEDS_INSTALL (
    echo     Installing dependencies...
    "%VPY%" -m pip install --upgrade pip --quiet || exit /b 1
    "%VPY%" -m pip install -r requirements.txt pyinstaller --quiet || (
        echo ERROR: dependency installation failed.
        exit /b 1
    )
)

"%VPY%" -c "import PyQt6.QtWidgets, pymupdf, PIL, PyInstaller" >nul 2>&1 || (
    echo ERROR: the environment is still incomplete after installation.
    exit /b 1
)
echo     Dependencies: OK

rem ---------------------------------------------------------------- 2/5 ---
rem Shipping a binary that fails its own interoperability vectors is worse
rem than not shipping one.
echo [2/5] Running tests...
"%VPY%" -m unittest discover -s tests -t . -b >nul 2>&1 || (
    echo ERROR: tests failed. Run for details:
    echo        "%VPY%" -m unittest discover -s tests -t . -v
    exit /b 1
)
echo     Tests: OK

rem ---------------------------------------------------------------- 3/5 ---
echo [3/5] Clearing previous output...
if exist dist rmdir /s /q dist
if exist build\PDFteleporter rmdir /s /q build\PDFteleporter

rem ---------------------------------------------------------------- 4/5 ---
echo [4/5] Building with PyInstaller...
"%VPY%" -m PyInstaller --noconfirm --clean pdfteleporter.spec || exit /b 1

rem The failure that produced a broken build: everything reported success and
rem the application raised ModuleNotFoundError on launch.
if not exist "dist\PDFteleporter\_internal\pymupdf" (
    echo ERROR: PyMuPDF was not bundled. The build would fail at launch.
    exit /b 1
)
echo     PyMuPDF bundled: OK

for /f "usebackq" %%S in (`powershell -NoProfile -Command ^
    "(Get-ChildItem -Recurse dist\PDFteleporter | Measure-Object Length -Sum).Sum/1MB -as [int]"`) do set "SIZE=%%S"
echo     Output size: !SIZE! MB
if !SIZE! GTR 250 (
    echo     WARNING: unexpectedly large. Check that the spec exclusions still
    echo              cover everything installed in this environment.
)

rem Optional Authenticode signing, before the executables are packaged. Set
rem SIGNTOOL and SIGN_ARGS in the environment; skipped silently when unset.
if defined SIGNTOOL (
    echo     Signing executables...
    "%SIGNTOOL%" sign %SIGN_ARGS% "dist\PDFteleporter\PDFteleporter.exe" || exit /b 1
    "%SIGNTOOL%" sign %SIGN_ARGS% "dist\PDFteleporter\psditool.exe" || exit /b 1
) else (
    echo     Not signing ^(SIGNTOOL not set^). The installer will trigger
    echo     SmartScreen on first run. See README, "Signature de code".
)

rem ---------------------------------------------------------------- 5/5 ---
if not defined WANT_INSTALLER (
    echo [5/5] Skipped installer ^(pass "full" to build it^).
    echo.
    echo Done: dist\PDFteleporter\PDFteleporter.exe
    exit /b 0
)

echo [5/5] Building the installer with Inno Setup...
set "ISCC="
for %%P in (
    "%ProgramFiles%\Inno Setup 7\iscc.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\iscc.exe"
) do if exist %%P set "ISCC=%%~P"
if not defined ISCC (
    where iscc.exe >nul 2>&1 && for /f "delims=" %%P in ('where iscc.exe') do set "ISCC=%%P"
)
if not defined ISCC (
    echo ERROR: iscc.exe not found. Install Inno Setup 7 or add it to PATH.
    echo        Inno Setup 6 cannot compile this script: it uses
    echo        SetupArchitecture, which exists only from version 7.
    exit /b 1
)

echo     Using: !ISCC!
"!ISCC!" installer\pdfteleporter.iss || exit /b 1

rem A checksum lets a receiving station confirm the download before running an
rem unsigned installer.
for %%F in (dist\installer\*.exe) do (
    for /f "skip=1 tokens=1" %%H in ('certutil -hashfile "%%F" SHA256 ^| findstr /r "^[0-9a-f]"') do (
        > "%%F.sha256" echo %%H  %%~nxF
        echo     SHA-256: %%H
    )
)

echo.
echo Done: dist\installer\
dir /b dist\installer
