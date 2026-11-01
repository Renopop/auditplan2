@echo off
chcp 65001 >nul 2>&1
title Build - Audit Staffing Optimizer
setlocal

echo.
echo  ╔════════════════════════════════════════════════════════╗
echo  ║     CREATION DE L'EXECUTABLE - AUDIT STAFFING          ║
echo  ╚════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: 1/4 Python
echo [1/4] Verification de Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERREUR] Python n'est pas installe ou n'est pas dans le PATH.
    echo  Installez Python 3.10+ depuis https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  [OK] Python %PYVER% detecte
echo.

:: 2/4 Environnement virtuel dedie au build
echo [2/4] Preparation de l'environnement de build...
if not exist ".venv-build" (
    python -m venv .venv-build
    if errorlevel 1 (
        echo  [ERREUR] Echec de la creation de l'environnement virtuel
        pause
        exit /b 1
    )
)
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip --quiet >nul 2>&1
pip install -r requirements.txt pyinstaller --progress-bar on
if errorlevel 1 (
    echo  [ERREUR] L'installation des dependances a echoue.
    echo  Verifiez votre connexion internet et reessayez.
    pause
    exit /b 1
)
echo  [OK] Dependances et PyInstaller installes
echo.

:: 3/4 Compilation
echo [3/4] Compilation avec PyInstaller (plusieurs minutes)...
if exist "dist\AuditPlan.exe" del /q "dist\AuditPlan.exe"
pyinstaller --noconfirm --clean AuditPlan.spec
if errorlevel 1 (
    echo.
    echo  [ERREUR] La compilation a echoue. Consultez les messages ci-dessus.
    pause
    exit /b 1
)
echo.

:: 4/4 Resultat
echo [4/4] Verification...
if not exist "dist\AuditPlan.exe" (
    echo  [ERREUR] dist\AuditPlan.exe n'a pas ete genere.
    pause
    exit /b 1
)
for %%A in ("dist\AuditPlan.exe") do set /a SIZE_MB=%%~zA/1048576
echo.
echo  ╔════════════════════════════════════════════════════════╗
echo  ║              EXECUTABLE CREE AVEC SUCCES !             ║
echo  ╚════════════════════════════════════════════════════════╝
echo.
echo  Fichier : %~dp0dist\AuditPlan.exe  (~%SIZE_MB% Mo)
echo.
echo  Copiez ce fichier seul sur le poste cible et double-cliquez dessus.
echo  Les dossiers data\ et logs\ seront crees a cote de l'executable.
echo.
pause
