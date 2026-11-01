@echo off
chcp 65001 >nul 2>&1
title Installation - Audit Staffing Optimizer

echo.
echo  ╔════════════════════════════════════════════════════════╗
echo  ║     INSTALLATION - AUDIT STAFFING OPTIMIZER            ║
echo  ╚════════════════════════════════════════════════════════╝
echo.

:: Vérifier si Python est installé
echo [1/4] Verification de Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERREUR] Python n'est pas installe ou n'est pas dans le PATH.
    echo  Veuillez installer Python 3.10+ depuis:
    echo  https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  [OK] Python %PYVER% detecte
echo.

:: Créer un environnement virtuel
echo [2/4] Configuration de l'environnement virtuel...
if exist ".venv" (
    echo  [INFO] Environnement existant detecte, mise a jour...
) else (
    echo  [INFO] Creation de l'environnement virtuel...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERREUR] Echec de la creation de l'environnement virtuel
        pause
        exit /b 1
    )
)
echo  [OK] Environnement virtuel pret
echo.

:: Activer et installer les dépendances
echo [3/4] Installation des dependances Python...
call .venv\Scripts\activate.bat

:: Mise à jour pip silencieuse
python -m pip install --upgrade pip --quiet >nul 2>&1

:: Installation des dépendances avec barre de progression
pip install -r requirements.txt --progress-bar on
if errorlevel 1 (
    echo.
    echo  [ERREUR] L'installation des dependances a echoue.
    echo  Verifiez votre connexion internet et reessayez.
    pause
    exit /b 1
)
echo  [OK] Dependances installees
echo.

:: Créer le template Excel si le script existe
echo [4/4] Creation des fichiers templates...
if exist "create_template.py" (
    python create_template.py >nul 2>&1
    echo  [OK] Template Excel cree dans templates/
) else (
    echo  [INFO] Pas de script de template trouve
)
echo.

echo  ╔════════════════════════════════════════════════════════╗
echo  ║          INSTALLATION TERMINEE AVEC SUCCES !           ║
echo  ╚════════════════════════════════════════════════════════╝
echo.
echo  Pour lancer l'application:
echo    - Double-cliquez sur launch.bat
echo    - Ou executez: launch.bat
echo.
echo  L'application s'ouvrira dans votre navigateur web.
echo.
pause
