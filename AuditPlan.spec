# -*- mode: python ; coding: utf-8 -*-
"""
Configuration PyInstaller : produit un exécutable Windows unique
(dist/AuditPlan.exe) contenant Python, Streamlit, OR-Tools et l'application.

Construction :  build.bat   (ou :  pyinstaller --noconfirm --clean AuditPlan.spec)
"""

import os
from PyInstaller.utils.hooks import collect_all, copy_metadata

PROJECT_DIR = os.path.abspath(SPECPATH)

# ---------------------------------------------------------------------------
# Modules du projet : embarqués comme fichiers (.py) car Streamlit exécute
# app.py depuis le disque, et comme imports cachés pour les autres modules.
# ---------------------------------------------------------------------------
PROJECT_MODULES = [
    "app",
    "auditor_manager",
    "constraints",
    "cp_optimizer",
    "excel_handler",
    "generate_demo_data",
    "genetic_optimizer",
    "logger",
    "models",
    "optimizer",
    "paths",
    "utils",
]

datas = [(os.path.join(PROJECT_DIR, f"{m}.py"), ".") for m in PROJECT_MODULES]
binaries = []
hiddenimports = list(PROJECT_MODULES)

# ---------------------------------------------------------------------------
# Bibliothèques nécessitant leurs fichiers de données (frontend Streamlit,
# bibliothèques natives OR-Tools, métadonnées de version…).
# ---------------------------------------------------------------------------
for package in ["streamlit", "ortools"]:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

for package in ["pandas", "numpy", "openpyxl", "altair", "pyarrow", "pydeck", "protobuf"]:
    try:
        datas += copy_metadata(package)
    except Exception:
        pass  # package absent : ignoré

hiddenimports += [
    "openpyxl",
    "openpyxl.styles",
    "openpyxl.utils",
    "pandas",
    "numpy",
    "multiprocessing",
    "multiprocessing.pool",
    "multiprocessing.spawn",
]

# Bibliothèques lourdes non utilisées par l'application : exclues pour
# réduire la taille du .exe.
excludes = [
    "pulp",
    "highspy",
    "matplotlib",
    "scipy",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
]

a = Analysis(
    [os.path.join(PROJECT_DIR, "run_app.py")],
    pathex=[PROJECT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AuditPlan",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX casse certaines DLL (numpy/ortools) : désactivé
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,         # fenêtre console : affiche l'adresse et permet Ctrl+C
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=os.path.join(PROJECT_DIR, "icon.ico"),  # décommenter pour une icône
)
