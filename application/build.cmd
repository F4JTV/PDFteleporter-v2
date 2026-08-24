@echo off
setlocal enabledelayedexpansion
rem ---------------------------------------------------------------------------
rem  Builds PDF Teleporter and its installer.
rem
rem    build.cmd          build the application only
rem    build.cmd full     build the application, then the installer
rem
rem  Requires: Python 3.10+ with the project dependencies, PyInstaller, and for
rem  the installer step Inno Setup 7 with iscc.exe reachable.
rem ---------------------------------------------------------------------------

cd /d "%~dp0"

echo [1/4] Checking the toolchain...
python --version >nul 2>&1 || (echo ERROR: python not found on PATH & exit /b 1)
for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)"') do set "PYEXE=%%P"
echo     Interpreter: !PYEXE!

rem Every dependency must be importable by THIS interpreter. PyInstaller
rem downgrades a missing hidden import to a warning and builds anyway, which
rem yields an executable that dies on its first import. Refuse up front.
python -c "import PyInstaller" >nul 2>&1 || (
    echo ERROR: PyInstaller not installed for this interpreter.
    echo        Run: "!PYEXE!" -m pip install pyinstaller
    exit /b 1
)
python -c "import pymupdf, PIL, PyQt6.QtWidgets" >nul 2>&1 || (
    echo ERROR: missing dependencies for this interpreter.
    echo        Run: "!PYEXE!" -m pip install -r requirements.txt
    echo        If you use a virtual environment, activate it first.
    exit /b 1
)

rem Run the test suite before building. Shipping a binary that fails its own
rem interoperability vectors is worse than not shipping one.
echo     Running tests...
python -m unittest discover -s tests -t . -b >nul 2>&1 || (
    echo ERROR: tests failed. Run for details:
    echo        python -m unittest discover -s tests -t . -v
    exit /b 1
)
echo     Tests: OK

echo [2/4] Clearing previous output...
if exist dist rmdir /s /q dist
if exist build\PDFteleporter rmdir /s /q build\PDFteleporter

echo [3/4] Building with PyInstaller...
python -m PyInstaller --noconfirm --clean pdfteleporter.spec || exit /b 1

rem Confirm the PDF engine really landed in the bundle. This is the failure
rem that produced a broken 2.0.0 build: everything reported success and the
rem application raised ModuleNotFoundError on launch.
if not exist "dist\PDFteleporter\_internal\pymupdf" (
    echo ERROR: PyMuPDF was not bundled. The build would fail at launch.
    exit /b 1
)
echo     PyMuPDF bundled: OK

rem A build that quietly picked up a heavy package would still "succeed", so
rem check the size and say so rather than shipping a 900 MB folder.
for /f "usebackq" %%S in (`powershell -NoProfile -Command ^
    "(Get-ChildItem -Recurse dist\PDFteleporter | Measure-Object Length -Sum).Sum/1MB -as [int]"`) do set SIZE=%%S
echo     Output size: !SIZE! MB
if !SIZE! GTR 250 (
    echo     WARNING: unexpectedly large. Check that the spec exclusions still
    echo              cover everything installed in this environment.
)

rem Optional Authenticode signing of the two executables, before they are
rem packed into the installer. Set SIGNTOOL and SIGN_ARGS in the environment;
rem if SIGNTOOL is not set this step is skipped silently.
if defined SIGNTOOL (
    echo     Signing executables...
    "%SIGNTOOL%" sign %SIGN_ARGS% "dist\PDFteleporter\PDFteleporter.exe" || exit /b 1
    "%SIGNTOOL%" sign %SIGN_ARGS% "dist\PDFteleporter\psditool.exe" || exit /b 1
) else (
    echo     Not signing ^(SIGNTOOL not set^). The installer will trigger
    echo     SmartScreen on first run. See README, "Code signing".
)

if /i not "%~1"=="full" (
    echo [4/4] Skipped installer ^(pass "full" to build it^).
    echo Done: dist\PDFteleporter\PDFteleporter.exe
    exit /b 0
)

echo [4/4] Building the installer with Inno Setup...
set "ISCC="
for %%P in (
    "%ProgramFiles%\Inno Setup 7\iscc.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\iscc.exe"
    "%ProgramFiles%\Inno Setup 6\iscc.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\iscc.exe"
) do if exist %%P set "ISCC=%%~P"
if not defined ISCC (
    where iscc.exe >nul 2>&1 && for /f "delims=" %%P in ('where iscc.exe') do set "ISCC=%%P"
)
if not defined ISCC (
    echo ERROR: iscc.exe not found. Install Inno Setup 7 or add it to PATH.
    exit /b 1
)

echo     Using: !ISCC!
"!ISCC!" installer\pdfteleporter.iss || exit /b 1

echo.
echo Done: dist\installer\
dir /b dist\installer
