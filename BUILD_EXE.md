# Créer l'exécutable Windows (AuditPlan.exe)

Ce guide explique comment produire un **fichier .exe unique** de l'application,
utilisable par double-clic sur un poste Windows **sans Python installé**.

## Prérequis (poste de compilation uniquement)

- Windows 10 ou 11, 64 bits
- Python 3.10 à 3.13 installé et présent dans le PATH
  (https://www.python.org/downloads/ : cocher « Add Python to PATH »)
- Connexion internet (téléchargement des dépendances)
- Environ 2 Go d'espace disque libre pendant la compilation

> L'exécutable doit être compilé **sur Windows** : PyInstaller ne fait pas de
> compilation croisée. Un .exe compilé sur une machine fonctionne ensuite sur
> tous les postes Windows 64 bits.

## Compilation

1. Ouvrir le dossier du projet dans l'Explorateur.
2. Double-cliquer sur **`build.bat`**.
3. Patienter : la première compilation prend 5 à 15 minutes
   (installation des dépendances, puis empaquetage).
4. Le fichier est généré dans **`dist\AuditPlan.exe`** (environ 200 à 350 Mo).

Équivalent en ligne de commande :

```bat
python -m venv .venv-build
.venv-build\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean AuditPlan.spec
```

## Distribution et utilisation

- Copier **uniquement** `dist\AuditPlan.exe` sur le poste cible (clé USB,
  réseau…). Aucun autre fichier n'est nécessaire.
- Double-cliquer sur `AuditPlan.exe` :
  - une fenêtre console s'ouvre et affiche l'adresse de l'application ;
  - le navigateur par défaut s'ouvre automatiquement sur l'application ;
  - le premier démarrage prend 10 à 30 secondes (décompression en mémoire).
- Pour arrêter l'application : fermer la fenêtre console (ou Ctrl+C).
- Les données saisies et les journaux sont enregistrés **à côté de
  l'exécutable**, dans les dossiers `data\` et `logs\`. Placer l'exe dans un
  dossier où l'utilisateur a le droit d'écrire (pas dans `Program Files`).

## Fichiers concernés

| Fichier | Rôle |
|---------|------|
| `run_app.py` | Point d'entrée : démarre Streamlit et ouvre le navigateur |
| `AuditPlan.spec` | Configuration PyInstaller (modules, données, exclusions) |
| `build.bat` | Script de compilation Windows |
| `paths.py` | Résout les chemins `data/` et `logs/` à côté de l'exe |

## Personnalisation

- **Icône** : placer un fichier `icon.ico` dans le projet et décommenter la
  ligne `icon=` à la fin de `AuditPlan.spec`.
- **Masquer la console** : passer `console=False` dans `AuditPlan.spec`.
  L'application ne pourra alors être arrêtée que depuis le Gestionnaire des
  tâches ; la console est conseillée.
- **Nom du fichier** : modifier `name="AuditPlan"` dans `AuditPlan.spec`.

## Problèmes fréquents

- **L'antivirus bloque ou supprime l'exe** : les exécutables PyInstaller non
  signés sont parfois signalés à tort. Ajouter une exclusion ou signer l'exe
  avec un certificat de code.
- **Démarrage lent** : normal en mode fichier unique (extraction dans un
  dossier temporaire à chaque lancement). Pour un démarrage plus rapide,
  remplacer dans `AuditPlan.spec` la section `EXE(...)` par un build en
  dossier (`COLLECT`), au prix d'un dossier de fichiers au lieu d'un exe seul.
- **Page blanche dans le navigateur** : attendre quelques secondes puis
  rafraîchir ; vérifier que le port affiché dans la console n'est pas bloqué
  par un pare-feu local.
- **Erreur `ModuleNotFoundError` au lancement** : ajouter le module manquant
  à la liste `hiddenimports` de `AuditPlan.spec` et recompiler.
- **Le port 8501 est occupé** : le lanceur choisit automatiquement le port
  libre suivant (8502, 8503…).
