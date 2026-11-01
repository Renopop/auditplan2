"""
Point d'entrée de l'exécutable Audit Staffing Optimizer.

Ce script remplace la commande « streamlit run app.py » : il démarre le
serveur Streamlit dans le processus courant, puis ouvre le navigateur sur
l'adresse locale de l'application. Il est utilisé par PyInstaller
(voir AuditPlan.spec) pour produire un .exe unique.

Il peut aussi être lancé directement :  python run_app.py
"""

import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser

# Indispensable pour le multiprocessing (optimiseur génétique) dans un .exe :
# les processus enfants relancent l'exécutable et doivent s'arrêter ici.
multiprocessing.freeze_support()

from paths import get_base_dir, get_resource_dir, is_frozen  # noqa: E402

APP_NAME = "Audit Staffing Optimizer"
DEFAULT_PORT = 8501


def find_free_port(start: int = DEFAULT_PORT, attempts: int = 50) -> int:
    """Retourne le premier port libre à partir de `start`."""
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Aucun port libre trouvé pour démarrer l'application.")


def wait_and_open_browser(url: str, port: int, timeout: float = 60.0) -> None:
    """Attend que le serveur réponde sur `port`, puis ouvre le navigateur."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.3)


def print_banner(url: str) -> None:
    print()
    print("  ============================================================")
    print(f"   {APP_NAME}")
    print("  ============================================================")
    print()
    print("   L'application s'ouvre dans votre navigateur.")
    print(f"   Adresse : {url}")
    print()
    print(f"   Donnees et logs : {get_base_dir()}")
    print()
    print("   Pour arreter : fermez cette fenetre ou appuyez sur Ctrl+C.")
    print()
    sys.stdout.flush()


def main() -> None:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    app_path = os.path.join(get_resource_dir(), "app.py")
    if not os.path.exists(app_path):
        print(f"[ERREUR] Fichier introuvable : {app_path}")
        sys.exit(1)

    # Permet à app.py d'importer les modules du projet quand il est exécuté
    # par Streamlit depuis le dossier temporaire de l'exécutable.
    if get_resource_dir() not in sys.path:
        sys.path.insert(0, get_resource_dir())

    print_banner(url)

    threading.Thread(
        target=wait_and_open_browser, args=(url, port), daemon=True
    ).start()

    from streamlit.web import bootstrap

    # Options de configuration, au format attendu par load_config_options
    # (clés « section_option », comme les drapeaux de la ligne de commande).
    # global_developmentMode=False est indispensable dans l'exécutable : sans
    # cela Streamlit se croit en mode développement (port 3000, pas de
    # fichiers statiques servis, logs de debug).
    flag_options = {
        "global_developmentMode": False,
        "server_port": port,
        "server_address": "127.0.0.1",
        "server_headless": True,
        "server_fileWatcherType": "none",
        "server_runOnSave": False,
        "browser_gatherUsageStats": False,
        "logger_level": "info",
        "client_toolbarMode": "minimal",  # masque le bouton « Deploy »
    }
    bootstrap.load_config_options(flag_options)

    # Streamlit s'attend à un argv de la forme « streamlit run app.py »
    sys.argv = ["streamlit", "run", app_path]
    bootstrap.run(app_path, False, [], flag_options)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n   Application arretee.")
    except Exception as exc:  # affiche l'erreur avant la fermeture de la console
        print(f"\n[ERREUR] {exc}")
        if is_frozen():
            input("\nAppuyez sur Entree pour fermer...")
        raise
