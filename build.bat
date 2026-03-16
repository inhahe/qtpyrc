@echo off
REM Build a standalone qtpyrc distribution using PyInstaller.
REM Output goes to dist\qtpyrc\ — zip that directory to distribute.
REM
REM Prerequisites:
REM   pip install pyinstaller
REM
REM Usage:
REM   build.bat           (build normally)
REM   build.bat clean     (remove build artifacts)

if "%1"=="clean" (
    echo Cleaning build artifacts...
    rmdir /s /q build 2>nul
    rmdir /s /q dist 2>nul
    del /q qtpyrc.spec 2>nul
    echo Done.
    exit /b 0
)

echo Checking for PyInstaller...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

echo Building qtpyrc...
pyinstaller ^
    --name qtpyrc ^
    --noconfirm ^
    --windowed ^
    --add-data "defaults;defaults" ^
    --add-data "icons;icons" ^
    --add-data "plugins;plugins" ^
    --add-data "settings;settings" ^
    --add-data "docs;docs" ^
    --hidden-import qasync ^
    --hidden-import ruamel.yaml ^
    --hidden-import ruamel.yaml.clib ^
    --collect-submodules ruamel.yaml ^
    qtpyrc.py

if errorlevel 1 (
    echo Build FAILED.
    exit /b 1
)

echo.
echo Creating zip file...
pushd dist
if exist qtpyrc.zip del /q qtpyrc.zip
powershell -Command "Compress-Archive -Path 'qtpyrc' -DestinationPath 'qtpyrc.zip'"
popd

if exist dist\qtpyrc.zip (
    echo.
    echo Build complete!
    echo   Directory: dist\qtpyrc\
    echo   Zip file:  dist\qtpyrc.zip
) else (
    echo.
    echo Build complete! (zip creation failed, but directory is ready)
    echo   Directory: dist\qtpyrc\
)
echo.
echo To use:
echo   1. Unzip dist\qtpyrc.zip (or copy dist\qtpyrc\) wherever you want
echo   2. Run: qtpyrc\qtpyrc.exe --init myconfig
echo   3. Edit myconfig\config.yaml
echo   4. Run: qtpyrc\qtpyrc.exe -c myconfig\config.yaml
