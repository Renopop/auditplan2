"""
Résolution des chemins de l'application.

Lorsque l'application est empaquetée en exécutable (PyInstaller, mode
--onefile), le code source est extrait dans un dossier temporaire supprimé
à la fermeture. Les fichiers que l'application écrit (données JSON, logs)
doivent donc être placés à côté de l'exécutable, et non à côté du code.

- get_base_dir()     : dossier persistant où écrire data/ et logs/
                       (dossier du .exe si gelé, sinon dossier du projet)
- get_resource_dir() : dossier où lire les fichiers embarqués en lecture seule
                       (_MEIPASS si gelé, sinon dossier du projet)
"""

import os
import sys


def is_frozen() -> bool:
    """Retourne True si l'application tourne depuis un exécutable PyInstaller."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_base_dir() -> str:
    """Dossier persistant de l'application (à côté du .exe ou du code source)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir() -> str:
    """Dossier contenant les fichiers embarqués (code, exemples)."""
    if is_frozen():
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))
