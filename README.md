# Optimisation du Staffing des Missions d'Audit

Application Python/Streamlit pour optimiser l'allocation des ressources (auditeurs) aux missions d'audit sur une année, sous contraintes de disponibilité, compétences et périodes.

## Fonctionnalités

- **Import Excel** : Chargement des missions et auditeurs depuis un fichier Excel
- **Saisie manuelle** : Ajout de missions et auditeurs via l'interface
- **Optimisation sous contraintes** :
  - Respect des budgets jours par mission
  - Respect des capacités des auditeurs
  - Correspondance des compétences (IT, Modèle, Assurance, Généraliste)
  - Disponibilité des auditeurs (congés, etc.)
  - Périodes bloquées (août, 15-30 décembre)
  - Nombre d'auditeurs requis par mission
  - Affectation des superviseurs
- **Ressources externes** : Proposition automatique de ressources de backup (205j/an) si les ressources internes sont insuffisantes
- **Export Excel** : Planning sur 52 semaines avec noms des auditeurs et superviseurs

## Installation

```bash
# Cloner le dépôt
git clone <url-du-repo>
cd AuditPlan

# Créer un environnement virtuel
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows

# Installer les dépendances
pip install -r requirements.txt
```

## Exécutable Windows (.exe)

Pour distribuer l'application sous forme d'un **fichier .exe unique**, sans
installation de Python sur le poste cible, lancer `build.bat` sur un poste
Windows. Le fichier `dist\AuditPlan.exe` est généré en quelques minutes.
Voir [BUILD_EXE.md](BUILD_EXE.md) pour le détail.

## Utilisation

### Lancer l'application

```bash
streamlit run app.py
```

L'application s'ouvre dans votre navigateur à l'adresse `http://localhost:8501`.

### Format du fichier Excel d'entrée

Le fichier doit contenir deux feuilles :

#### Feuille "Missions"

| Colonne | Description | Exemple |
|---------|-------------|---------|
| Nom Mission | Nom unique de la mission | Audit Crédit Immobilier |
| Type Période | Trimestre, Mois, ou Semaine | Trimestre |
| Période | Valeur de la période | Q1, Mars-Avril, S15-S20 |
| Budget (jours) | Jours-homme totaux | 30 |
| Superviseur | Nom du superviseur | Martin |
| Nb Auditeurs | Nombre d'auditeurs requis | 2 |
| Compétences Requises | IT, Modèle, Assurance, Généraliste | IT, Assurance |

#### Feuille "Auditeurs"

| Colonne | Description | Exemple |
|---------|-------------|---------|
| Nom | Nom unique de l'auditeur | Martin |
| Budget (jours) | Jours disponibles par an | 205 |
| Compétences | Compétences séparées par virgule | IT, Généraliste |
| Indisponibilités | Périodes d'indisponibilité | S30-S35, août |
| Peut Superviser | Oui/Non | Oui |

### Formats des périodes

- **Trimestre** : Q1, Q2, Q3, Q4 ou T1, T2, T3, T4
- **Mois** : Janvier, Février, etc. ou Janvier-Mars
- **Semaine** : S12, S12-S16

### Formats des indisponibilités

- **Semaines** : S12-S16, S30
- **Mois** : août, juillet
- **Dates** : 15/08-30/08

## Contraintes automatiques

L'optimiseur applique automatiquement :

- **Pas de mission en août** (semaines 31-35)
- **Pas de mission du 15 au 30 décembre** (semaines 51-52)

## Architecture technique

```
AuditPlan/
├── app.py              # Interface Streamlit
├── models.py           # Modèles de données (Mission, Auditor, Skill)
├── cp_optimizer.py     # Moteur d'optimisation CP-SAT (OR-Tools) — RECOMMANDÉ
├── optimizer.py        # Façade d'optimisation + heuristique de repli
├── genetic_optimizer.py# Algorithme génétique (mode alternatif)
├── constraints.py      # Gestion des contraintes
├── excel_handler.py    # Lecture/écriture Excel
├── utils.py            # Utilitaires divers
├── requirements.txt    # Dépendances Python
└── templates/          # Templates Excel
```

### Modèle d'optimisation (CP-SAT, recommandé)

Le moteur par défaut utilise **CP-SAT (Google OR-Tools)**, un solveur exact et
multi-cœurs, avec une **formulation par blocs** beaucoup plus efficace que
l'ancienne approche jour-par-jour. Résultat : solution **optimale prouvée** en
quelques secondes (≈ 1 s pour 50 missions, ≈ 10 s pour 120 missions) au lieu de
plusieurs minutes.

**Idée clé — formulation par blocs** : le calendrier d'une mission est représenté
par le *choix d'un bloc de semaines contigu* (un booléen par placement possible),
ce qui garantit la contiguïté et l'absence de trous *par construction*, sans
contraintes supplémentaires. La sous-traitance est un simple compteur entier par
mission, ce qui réduit drastiquement la taille du modèle.

**Variables de décision** :
- `blk[m,b]` : choix du bloc de semaines `b` pour la mission `m`
- `assign[m,a]` : 1 si l'auditeur interne `a` est dans l'équipe de `m`
- `ext[m]` : nombre de slots sous-traités sur la mission `m`

**Fonction objectif** (hiérarchie pondérée) :
1. **Minimiser la sous-traitance** (priorité absolue)
2. **Charger chaque auditeur ≥ 80 %** de sa capacité (seuil paramétrable)
3. **Respecter les préférences opt-in** (participation superviseur, chef désigné)
4. **Maximiser l'adéquation des compétences**

**Contraintes dures** :
1. Budget mission respecté (bloc dimensionné au budget)
2. Capacité auditeur respectée (budget en jours)
3. Disponibilité par semaine (couplage timing ↔ affectation, congés/maladie)
4. Période mission respectée + semaines bloquées (août, 15-30 déc.)
5. Un auditeur = une seule mission active par semaine
6. Nombre exact d'auditeurs par mission
7. Couverture de chaque compétence requise
8. Plafond de sous-traitance optionnel (curseur en jours dans l'interface, avec
   diagnostic du minimum requis si le plafond est trop bas)

### Rôles : superviseur (validateur), chef de mission, participation

Le modèle distingue trois rôles, conçus pour **décontraindre** le staffing :

- **Superviseur = validateur** : dernier relecteur qui valide la mission. Il
  **ne consomme aucune ressource** et n'impose aucune contrainte de planning
  (3 superviseurs peuvent ainsi valider 90 missions). Colonne Excel `Superviseur`.
- **Chef de mission** : désigné **parmi l'équipe affectée** à la mission. Par
  défaut l'optimiseur choisit le mieux qualifié ; on peut l'imposer via la
  colonne `Chef de Mission`. Marqué `(S)` dans le planning.
- **Participation ponctuelle du superviseur** : colonne `Superviseur Participe`
  (`Oui`/`Non`) pour allouer le superviseur comme **ressource** d'une mission
  précise (préférence forte, jamais bloquante).

Le seuil de charge minimale s'applique aux **exécutants** (les
superviseurs/validateurs en sont exclus). Il est **réglable dans l'interface**
(curseur « Taux d'occupation minimal des auditeurs », 0–100 %, 80 % par défaut)
et transmis au solveur via le paramètre `min_load_ratio`.

> **Note** : si OR-Tools n'est pas installé, l'application bascule
> automatiquement sur une heuristique gloutonne, puis sur l'algorithme génétique
> ou MILP selon le mode choisi.

### Jeu de données de démonstration

`generate_demo_data.py` produit `exemple_demo_complexe.xlsx`, un cas **complexe
et réaliste** prêt à importer : auditeurs (dont superviseurs/validateurs, vrais
noms), fermeture d'août + **6 semaines de congés (dont 3 en août) et 1 semaine
de maladie par auditeur**, missions aléatoires, goulot d'étranglement
« Modèle » volontaire.

Le nombre d'auditeurs, de superviseurs et de missions est **paramétrable** :

```bash
# Scénario par défaut (40 auditeurs, 3 superviseurs, 90 missions)
python generate_demo_data.py

# Scénario personnalisé
python generate_demo_data.py --auditeurs 60 --superviseurs 5 --missions 120 \
    --sortie mon_scenario.xlsx

# Options : -a/--auditeurs  -s/--superviseurs  -m/--missions
#           --seed (reproductibilité)  -o/--sortie (fichier de sortie)
python generate_demo_data.py --help
```

## Résultats

Le planning des **auditeurs (exécutants)** et celui des **superviseurs
(validateurs)** sont présentés **séparément**, à l'écran comme dans l'export
Excel. L'application génère un fichier Excel avec :

1. **Feuille Planning** : Matrice missions × semaines avec noms des auditeurs
   - `(S)` = Chef de mission
   - `[EXT]` = Ressource externe (sous-traitance)

2. **Feuille Planning Auditeurs** : un auditeur par ligne × 52 semaines
   (superviseurs **exclus**).

3. **Feuille Planning Superviseurs** : un superviseur par ligne × 52 semaines,
   missions validées par semaine (`(P)` = le superviseur participe aussi comme
   ressource).

4. **Feuille Résumé** : statistiques par mission et par auditeur.

5. **Feuille Légende** : couleurs et symboles.

## Compétences

L'application gère 4 profils de compétences cumulables :

- **IT** : Audit des systèmes d'information
- **Modèle** : Audit des modèles (Bâle, etc.)
- **Assurance** : Audit conformité et assurance
- **Généraliste** : Peut intervenir sur tout type de mission

## Licence

MIT License
