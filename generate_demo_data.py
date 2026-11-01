#!/usr/bin/env python3
"""
Générateur de jeu de données de démonstration COMPLEXE pour Audit Staffing.

Produit un fichier Excel directement ingérable par l'application :
  - 40 auditeurs (vrais noms/prénoms), dont 3 superviseurs
  - Fermeture du cabinet en août (indisponibilité commune)
  - Congés individuels variés
  - 90 missions aléatoires, périodes variées (trimestres, mois, semaines)
  - Compétences variées avec un goulot d'étranglement volontaire (Modèle/Bâle rare)
  - Charge globale élevée et 3 superviseurs pour 90 missions
    -> cas difficile qui démontre la puissance et la validité du solveur.

Usage:
    python generate_demo_data.py [chemin_sortie.xlsx]
"""

import random
import sys

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SEED = 42  # reproductible (données "aléatoires" mais déterministes)

# --------------------------------------------------------------------------- #
#  Noms / prénoms français                                                    #
# --------------------------------------------------------------------------- #
PRENOMS = [
    "Camille", "Lucas", "Emma", "Hugo", "Léa", "Nathan", "Chloé", "Louis",
    "Manon", "Jules", "Inès", "Gabriel", "Sarah", "Raphaël", "Jade", "Arthur",
    "Louise", "Adam", "Alice", "Paul", "Lina", "Thomas", "Anna", "Maxime",
    "Juliette", "Antoine", "Clara", "Théo", "Eva", "Romain", "Zoé", "Quentin",
    "Margaux", "Victor", "Nina", "Alexandre", "Élise", "Mathis", "Aurélie",
    "Benjamin", "Sophie", "Florian", "Marine", "Julien", "Céline", "Damien",
]
NOMS = [
    "Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit",
    "Durand", "Leroy", "Moreau", "Simon", "Laurent", "Lefebvre", "Michel",
    "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier", "Morel",
    "Girard", "André", "Lefèvre", "Mercier", "Dupont", "Lambert", "Bonnet",
    "François", "Martinez", "Legrand", "Garnier", "Faure", "Rousseau", "Blanc",
    "Guerin", "Muller", "Henry", "Roussel", "Nicolas", "Perrin", "Morin",
    "Mathieu", "Clément", "Gauthier", "Dumont",
]

# --------------------------------------------------------------------------- #
#  Compétences (avec rareté volontaire pour le goulot d'étranglement)         #
# --------------------------------------------------------------------------- #
SKILLS = ["IT", "Modèle", "Assurance", "Généraliste"]
# Probabilité de possession d'une compétence "primaire".
# Modèle (bâlois) reste la compétence la plus rare -> tension réaliste, mais en
# quantité suffisante pour qu'une bonne solution existe.
SKILL_WEIGHTS = {
    "IT": 0.35,
    "Modèle": 0.22,        # compétence rare (modèles bâlois) mais couvrable
    "Assurance": 0.35,
    "Généraliste": 0.50,
}

# Thèmes de missions d'audit (banque / assurance / IT / modèles)
MISSION_THEMES = [
    ("Audit Crédit Immobilier", ["Assurance", "Généraliste"]),
    ("Audit Crédit Conso", ["Assurance"]),
    ("Revue Modèle PD", ["Modèle"]),
    ("Revue Modèle LGD", ["Modèle"]),
    ("Backtesting Modèle IRB", ["Modèle"]),
    ("Validation Modèle IFRS9", ["Modèle", "Assurance"]),
    ("Audit Sécurité SI", ["IT"]),
    ("Audit Cybersécurité", ["IT"]),
    ("Audit Cloud & Datacenter", ["IT"]),
    ("Audit Continuité Activité", ["IT", "Généraliste"]),
    ("Audit Conformité LCB-FT", ["Assurance", "Généraliste"]),
    ("Audit Conformité MIF2", ["Assurance"]),
    ("Audit RGPD", ["IT", "Assurance"]),
    ("Audit Processus Comptable", ["Généraliste"]),
    ("Audit Trésorerie", ["Assurance", "Généraliste"]),
    ("Audit Risque de Marché", ["Modèle", "Assurance"]),
    ("Audit Risque de Liquidité", ["Modèle"]),
    ("Audit Gouvernance", ["Généraliste"]),
    ("Audit Achats & Fournisseurs", ["Généraliste"]),
    ("Audit Reporting Prudentiel", ["Assurance", "Modèle"]),
    ("Audit Gestion d'Actifs", ["Assurance"]),
    ("Audit Données & Qualité", ["IT", "Modèle"]),
    ("Audit ESG / Durabilité", ["Généraliste", "Assurance"]),
    ("Audit Filiale Internationale", ["Généraliste"]),
    ("Audit Moyens de Paiement", ["IT", "Assurance"]),
]


def weighted_skills(rng):
    """
    Tire les compétences d'un auditeur.

    Tout auditeur sait faire de l'audit généraliste (compétence de base), et
    possède en plus 0 à 2 spécialités (IT / Modèle / Assurance). Cela reflète la
    réalité (un spécialiste peut aussi mener des missions générales) et rend la
    main-d'œuvre fongible -> on peut charger chacun à 80%. Le goulot "Modèle"
    subsiste car la COUVERTURE d'une compétence requiert un vrai spécialiste.
    """
    specialties = [s for s in SKILLS if s != "Généraliste"]
    chosen = {"Généraliste"}
    for sk in specialties:
        if rng.random() < SKILL_WEIGHTS[sk]:
            chosen.add(sk)
    # Au plus 2 spécialités (+ généraliste)
    spec = [s for s in chosen if s != "Généraliste"]
    if len(spec) > 2:
        spec = rng.sample(spec, 2)
        chosen = {"Généraliste", *spec}
    return chosen


AUGUST_WEEKS = [31, 32, 33, 34, 35]          # semaines d'août
# Semaines de congé/maladie possibles hors août et hors fermeture 15-30 déc. (S51-52)
WORK_WEEKS = [w for w in range(2, 51) if w not in AUGUST_WEEKS]


def pick_unavailabilities(rng):
    """
    Indisponibilités par auditeur :
      - 6 semaines de congés payés au total, dont 3 EN AOÛT (bloc) + 3 ailleurs
      - au moins 1 semaine d'arrêt maladie, placée aléatoirement
    Renvoie la liste triée des numéros de semaine (uniques).
    """
    weeks = set()

    # 3 semaines de congés en août (bloc de 3 dans la plage 31-35)
    aug_start = rng.choice([31, 32, 33])
    weeks.update([aug_start, aug_start + 1, aug_start + 2])

    # 3 semaines de congés payés ailleurs (1 ou 2 blocs)
    remaining = 3
    while remaining > 0:
        block = min(remaining, rng.choice([1, 2, 2, 3]))
        start = rng.choice(WORK_WEEKS)
        candidate = list(range(start, start + block))
        if all(w in WORK_WEEKS and w not in weeks for w in candidate):
            weeks.update(candidate)
            remaining -= block

    # 1 semaine d'arrêt maladie (aléatoire, isolée)
    for _ in range(50):
        sick = rng.choice(WORK_WEEKS)
        if sick not in weeks:
            weeks.add(sick)
            break

    return sorted(weeks)


def _format_weeks(weeks):
    """Formate une liste de semaines en plages compactes : 'S5-S7, S12, S31-S33'."""
    if not weeks:
        return ""
    parts = []
    start = prev = weeks[0]
    for w in weeks[1:]:
        if w == prev + 1:
            prev = w
        else:
            parts.append(f"S{start}-S{prev}" if start != prev else f"S{start}")
            start = prev = w
    parts.append(f"S{start}-S{prev}" if start != prev else f"S{start}")
    return ", ".join(parts)


def random_period(rng):
    """
    Période variée : trimestre, mois, ou plage de semaines (jamais 100% août).
    Renvoie (chaîne_période, PeriodType) pour pouvoir calculer les semaines réelles.
    """
    from models import PeriodType
    kind = rng.random()
    if kind < 0.5:
        return rng.choice(["Q1", "Q2", "Q3", "Q4"]), PeriodType.TRIMESTRE
    elif kind < 0.8:
        mois = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
                "Juillet", "Septembre", "Octobre", "Novembre"]
        if rng.random() < 0.6:
            return rng.choice(mois), PeriodType.MOIS
        i = rng.randint(0, len(mois) - 2)
        return f"{mois[i]}-{mois[i + 1]}", PeriodType.MOIS
    else:
        zones = [(2, 28), (37, 48)]
        lo, hi = rng.choice(zones)
        start = rng.randint(lo, hi - 5)
        length = rng.randint(3, 6)
        return f"S{start}-S{start + length}", PeriodType.SEMAINE


def _max_contiguous_run(weeks_set):
    """Plus longue suite de semaines consécutives dans l'ensemble."""
    if not weeks_set:
        return 0
    ws = sorted(weeks_set)
    best = cur = 1
    for i in range(1, len(ws)):
        if ws[i] == ws[i - 1] + 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


# --------------------------------------------------------------------------- #
#  Génération                                                                  #
# --------------------------------------------------------------------------- #
def generate(n_auditors=40, n_supervisors=3, n_missions=90, seed=SEED,
             num_weights=(0.04, 0.26, 0.34, 0.36), weeks_lo=3):
    rng = random.Random(seed)

    # ---- Noms uniques ----
    names = set()
    auditors = []
    while len(auditors) < n_auditors:
        full = f"{rng.choice(PRENOMS)} {rng.choice(NOMS)}"
        if full in names:
            continue
        names.add(full)
        auditors.append(full)

    # ---- Superviseurs : on garantit qu'ils couvrent les 3 compétences clés ----
    supervisor_names = auditors[:n_supervisors]
    forced_sup_skills = [
        {"IT", "Généraliste"},
        {"Modèle", "Assurance"},      # le superviseur "modèle" est précieux (rare)
        {"Assurance", "Généraliste"},
    ]

    auditor_rows = []
    for i, name in enumerate(auditors):
        is_sup = name in supervisor_names
        if is_sup:
            skills = forced_sup_skills[supervisor_names.index(name) % len(forced_sup_skills)]
            budget = 205
        else:
            skills = weighted_skills(rng)
            budget = rng.choice([170, 180, 190, 195, 200, 205])

        # Indisponibilités : 6 semaines de congés (dont 3 en août) + 1 sem. maladie
        unavail_weeks = pick_unavailabilities(rng)

        auditor_rows.append({
            "Nom": name,
            "Budget (jours)": budget,
            "Compétences": ", ".join(sorted(skills)),
            "Indisponibilités": _format_weeks(unavail_weeks),
            "Peut Superviser": "Oui" if is_sup else "Non",
        })

    # ---- Missions ----
    # On calcule les semaines réelles (août exclu) via la logique de l'app pour
    # garantir que la durée de chaque mission tient dans une plage CONTIGUË.
    from models import get_weeks_for_period, PlanningConfig
    config = PlanningConfig(year=2024)

    mission_rows = []
    for j in range(n_missions):
        theme, theme_skills = rng.choice(MISSION_THEMES)
        name = f"{theme} #{j + 1:02d}"

        # Compétences : thème + parfois une compétence supplémentaire
        skills = set(theme_skills)
        if rng.random() < 0.15:
            skills.add(rng.choice(SKILLS))
        if len(skills) > 2:
            skills = set(rng.sample(sorted(skills), 2))

        # Période + semaines réellement disponibles (contiguës)
        period, max_run = "Q1", 13  # repli sûr (trimestre Q1 = 13 semaines pleines)
        for _ in range(30):  # quelques essais si la plage est trop courte
            cand, ptype = random_period(rng)
            allowed = get_weeks_for_period(ptype, cand, 2024)
            run = _max_contiguous_run(allowed - config.blocked_weeks)
            if run >= 3:
                period, max_run = cand, run
                break

        num_auditors = rng.choices([1, 2, 3, 4], weights=list(num_weights))[0]
        # Durée bornée par la plage contiguë disponible -> mission toujours valide
        lo = weeks_lo if max_run >= weeks_lo else max_run
        weeks = rng.randint(lo, min(8, max_run)) if max_run >= lo else max_run
        budget = num_auditors * 5 * weeks  # budget cohérent (jours-homme)

        # Superviseur (validateur) : choisi parmi les superviseurs s'il en existe
        supervisor = rng.choice(supervisor_names) if supervisor_names else ""
        # Le superviseur (validateur) participe ponctuellement à ~12% des missions
        participe = "Oui" if (supervisor and rng.random() < 0.12) else "Non"

        mission_rows.append({
            "Nom Mission": name,
            "Période": period,
            "Budget (jours)": budget,
            "Superviseur": supervisor,
            "Superviseur Participe": participe,
            "Nb Auditeurs": num_auditors,
            "Compétences Requises": ", ".join(sorted(skills)),
        })

    return auditor_rows, mission_rows, supervisor_names


# --------------------------------------------------------------------------- #
#  Écriture Excel                                                              #
# --------------------------------------------------------------------------- #
def _style_header(ws, n_cols):
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    for col in range(1, n_cols + 1):
        c = ws.cell(row=1, column=col)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(col)].width = 24


def write_excel(path, auditor_rows, mission_rows):
    wb = Workbook()

    # Feuille Missions
    ws_m = wb.active
    ws_m.title = "Missions"
    m_headers = ["Nom Mission", "Période", "Budget (jours)",
                 "Superviseur", "Superviseur Participe", "Nb Auditeurs",
                 "Compétences Requises"]
    ws_m.append(m_headers)
    for r in mission_rows:
        ws_m.append([r[h] for h in m_headers])
    _style_header(ws_m, len(m_headers))
    ws_m.freeze_panes = "A2"

    # Feuille Auditeurs
    ws_a = wb.create_sheet("Auditeurs")
    a_headers = ["Nom", "Budget (jours)", "Compétences",
                 "Indisponibilités", "Peut Superviser"]
    ws_a.append(a_headers)
    for r in auditor_rows:
        ws_a.append([r[h] for h in a_headers])
    _style_header(ws_a, len(a_headers))
    ws_a.freeze_panes = "A2"

    wb.save(path)


# --------------------------------------------------------------------------- #
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Génère un scénario de staffing d'audit (fichier Excel "
                    "directement ingérable par l'application).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-a", "--auditeurs", type=int, default=40,
                        help="Nombre total d'auditeurs (superviseurs inclus)")
    parser.add_argument("-s", "--superviseurs", type=int, default=3,
                        help="Nombre de superviseurs/validateurs (parmi les auditeurs)")
    parser.add_argument("-m", "--missions", type=int, default=90,
                        help="Nombre de missions à générer")
    parser.add_argument("--seed", type=int, default=SEED,
                        help="Graine aléatoire (pour reproductibilité)")
    parser.add_argument("-o", "--sortie", default="exemple_demo_complexe.xlsx",
                        help="Chemin du fichier Excel de sortie")
    # 1er argument positionnel optionnel = chemin de sortie (rétro-compatibilité)
    parser.add_argument("sortie_pos", nargs="?", default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    out = args.sortie_pos or args.sortie

    # ---- Validation des paramètres ----
    max_names = len(PRENOMS) * len(NOMS)
    errors = []
    if args.auditeurs < 1:
        errors.append("Le nombre d'auditeurs doit être >= 1.")
    if args.superviseurs < 0:
        errors.append("Le nombre de superviseurs doit être >= 0.")
    if args.superviseurs >= args.auditeurs:
        errors.append(
            f"Le nombre de superviseurs ({args.superviseurs}) doit être strictement "
            f"inférieur au nombre d'auditeurs ({args.auditeurs}) — il faut des exécutants."
        )
    if args.missions < 1:
        errors.append("Le nombre de missions doit être >= 1.")
    if args.auditeurs > max_names:
        errors.append(
            f"Trop d'auditeurs demandés ({args.auditeurs}) : maximum {max_names} "
            f"noms uniques disponibles."
        )
    if errors:
        for e in errors:
            print(f"[ERREUR] {e}")
        sys.exit(1)

    auditor_rows, mission_rows, supervisors = generate(
        n_auditors=args.auditeurs,
        n_supervisors=args.superviseurs,
        n_missions=args.missions,
        seed=args.seed,
    )
    write_excel(out, auditor_rows, mission_rows)

    total_budget = sum(m["Budget (jours)"] for m in mission_rows)
    # Capacité d'EXÉCUTION = auditeurs hors superviseurs (validateurs)
    exec_capacity = sum(a["Budget (jours)"] for a in auditor_rows
                        if a["Peut Superviser"] != "Oui")
    n_modele = sum(1 for a in auditor_rows if "Modèle" in a["Compétences"])
    n_modele_missions = sum(1 for m in mission_rows if "Modèle" in m["Compétences Requises"])
    n_particip = sum(1 for m in mission_rows if m["Superviseur Participe"] == "Oui")

    print(f"Fichier généré : {out}")
    print(f"  Auditeurs              : {len(auditor_rows)} "
          f"(dont {len(supervisors)} superviseurs/validateurs)")
    print(f"  Superviseurs (valid.)  : {', '.join(supervisors)}")
    print(f"  Missions               : {len(mission_rows)}")
    print(f"  Budget missions        : {total_budget} j-h")
    print(f"  Capacité exécutants    : {exec_capacity} j-h "
          f"({100 * total_budget / exec_capacity:.0f}% de charge sur les exécutants)")
    print(f"  Spécialistes Modèle    : {n_modele} auditeurs pour "
          f"{n_modele_missions} missions (goulot volontaire)")
    print(f"  Sup. participants      : {n_particip} missions (opt-in)")
    print(f"  Fermeture août         : oui (3 sem. de congés + 1 sem. maladie / auditeur)")


if __name__ == "__main__":
    main()
