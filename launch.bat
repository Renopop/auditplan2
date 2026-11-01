@echo off
chcp 65001 >nul 2>&1
title Audit Staffing Optimizer

echo.
echo  ╔════════════════════════════════════════════════════════╗
echo  ║           AUDIT STAFFING OPTIMIZER                     ║
echo  ╚════════════════════════════════════════════════════════╝
echo.

:: Vérifier si l'environnement virtuel existe
if not exist ".venv\Scripts\activate.bat" (
    echo  [ERREUR] Environnement virtuel non trouve.
    echo.
    echo  Veuillez d'abord executer install.bat
    echo.
    pause
    exit /b 1
)

:: Activer l'environnement virtuel
call .venv\Scripts\activate.bat

echo  [INFO] Demarrage de l'application...
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  L'application va s'ouvrir dans votre navigateur.      │
echo  │                                                         │
echo  │  URL: http://localhost:8501                             │
echo  │                                                         │
echo  │  Pour arreter: Ctrl+C ou fermez cette fenetre           │
echo  └─────────────────────────────────────────────────────────┘
echo.

:: Lancer Streamlit avec options optimisées
streamlit run app.py ^
    --server.headless true ^
    --browser.gatherUsageStats false ^
    --server.fileWatcherType poll

:: Si Streamlit se ferme
echo.
echo  [INFO] Application arretee.
echo.
pause
