"""
Utilitaires pour le système d'optimisation de staffing d'audit.
"""

from typing import List, Dict, Set, Any
from datetime import date, timedelta
from models import Auditor, Mission, Allocation, OptimizationResult, Skill


def week_to_date_range(week: int, year: int = 2024) -> tuple:
    """
    Convertit un numéro de semaine en dates de début et fin.

    Returns:
        Tuple (date_debut, date_fin)
    """
    jan_first = date(year, 1, 1)
    # Trouver le premier jour de la semaine 1
    first_monday = jan_first - timedelta(days=jan_first.weekday())
    if jan_first.weekday() > 3:  # Si jan 1 est après jeudi
        first_monday += timedelta(days=7)

    week_start = first_monday + timedelta(weeks=week - 1)
    week_end = week_start + timedelta(days=4)  # Vendredi

    return week_start, week_end


def format_week_range(weeks: Set[int], year: int = 2024) -> str:
    """
    Formate un ensemble de semaines en chaîne lisible.

    Ex: {1, 2, 3, 5, 6} -> "S1-S3, S5-S6"
    """
    if not weeks:
        return "-"

    sorted_weeks = sorted(weeks)
    ranges = []
    start = sorted_weeks[0]
    end = start

    for w in sorted_weeks[1:]:
        if w == end + 1:
            end = w
        else:
            if start == end:
                ranges.append(f"S{start}")
            else:
                ranges.append(f"S{start}-S{end}")
            start = end = w

    # Ajouter le dernier range
    if start == end:
        ranges.append(f"S{start}")
    else:
        ranges.append(f"S{start}-S{end}")

    return ", ".join(ranges)


def calculate_utilization_stats(
    auditors: List[Auditor],
    allocations: List[Allocation]
) -> Dict[str, Dict[str, Any]]:
    """
    Calcule les statistiques d'utilisation des auditeurs.

    Returns:
        Dict avec stats par auditeur
    """
    stats = {}

    # Initialiser
    for auditor in auditors:
        stats[auditor.name] = {
            "budget": auditor.budget_days,
            "allocated": 0.0,
            "utilization": 0.0,
            "missions": [],
            "weeks_worked": set()
        }

    # Calculer
    for alloc in allocations:
        if alloc.auditor_name in stats:
            stats[alloc.auditor_name]["allocated"] += alloc.days
            stats[alloc.auditor_name]["weeks_worked"].add(alloc.week)
            if alloc.mission_name not in stats[alloc.auditor_name]["missions"]:
                stats[alloc.auditor_name]["missions"].append(alloc.mission_name)

    # Finaliser
    for name, data in stats.items():
        if data["budget"] > 0:
            data["utilization"] = round(data["allocated"] / data["budget"] * 100, 1)
        data["num_weeks"] = len(data["weeks_worked"])
        data["allocated"] = round(data["allocated"], 1)

    return stats


def validate_input_data(
    missions: List[Mission],
    auditors: List[Auditor]
) -> tuple:
    """
    Valide les données d'entrée.

    Returns:
        Tuple (is_valid, errors, warnings)
    """
    errors = []
    warnings = []

    # Vérifier les missions
    mission_names = set()
    for m in missions:
        if not m.name:
            errors.append("Une mission n'a pas de nom")
        elif m.name in mission_names:
            errors.append(f"Mission en doublon: '{m.name}'")
        mission_names.add(m.name)

        if m.budget_days <= 0:
            errors.append(f"Mission '{m.name}': budget invalide ({m.budget_days})")

        if not m.allowed_weeks:
            errors.append(f"Mission '{m.name}': aucune semaine autorisée")

        if m.num_auditors <= 0:
            errors.append(f"Mission '{m.name}': nombre d'auditeurs invalide")

    # Vérifier les auditeurs
    auditor_names = set()
    for a in auditors:
        if not a.name:
            errors.append("Un auditeur n'a pas de nom")
        elif a.name in auditor_names:
            errors.append(f"Auditeur en doublon: '{a.name}'")
        auditor_names.add(a.name)

        if a.budget_days <= 0:
            warnings.append(f"Auditeur '{a.name}': budget nul ou négatif")

    # Vérifier la capacité globale
    total_mission_days = sum(m.budget_days for m in missions)
    total_auditor_days = sum(a.budget_days for a in auditors)

    if total_mission_days > total_auditor_days:
        deficit = total_mission_days - total_auditor_days
        warnings.append(
            f"Capacité potentiellement insuffisante: "
            f"{total_mission_days:.0f}j requis, {total_auditor_days:.0f}j disponibles "
            f"(déficit: {deficit:.0f}j)"
        )

    return len(errors) == 0, errors, warnings


def get_skill_distribution(auditors: List[Auditor]) -> Dict[Skill, int]:
    """
    Compte le nombre d'auditeurs par compétence.
    """
    distribution = {skill: 0 for skill in Skill}

    for auditor in auditors:
        for skill in auditor.skills:
            distribution[skill] += 1

    return distribution


def get_mission_requirements(missions: List[Mission]) -> Dict[Skill, float]:
    """
    Calcule le budget total par compétence requise.
    """
    requirements = {skill: 0.0 for skill in Skill}

    for mission in missions:
        for skill in mission.required_skills:
            requirements[skill] += mission.budget_days

    return requirements


def analyze_feasibility(
    missions: List[Mission],
    auditors: List[Auditor]
) -> Dict[str, Any]:
    """
    Analyse préliminaire de la faisabilité du plan.
    """
    analysis = {
        "total_mission_days": sum(m.budget_days for m in missions),
        "total_auditor_days": sum(a.budget_days for a in auditors),
        "num_missions": len(missions),
        "num_auditors": len(auditors),
        "skill_coverage": {},
        "bottlenecks": [],
        "recommendations": []
    }

    # Analyse par compétence
    skill_dist = get_skill_distribution(auditors)
    skill_req = get_mission_requirements(missions)

    for skill in Skill:
        available = skill_dist[skill] * 205  # Estimation moyenne
        required = skill_req[skill]
        analysis["skill_coverage"][skill.value] = {
            "auditors": skill_dist[skill],
            "required_days": required,
            "estimated_capacity": available,
            "status": "OK" if available >= required else "ATTENTION"
        }

        if required > available and required > 0:
            analysis["bottlenecks"].append(
                f"Compétence '{skill.value}': {required:.0f}j requis, "
                f"~{available:.0f}j disponibles"
            )

    # Recommandations
    if analysis["total_mission_days"] > analysis["total_auditor_days"]:
        analysis["recommendations"].append(
            "Envisager des ressources externes pour combler le déficit"
        )

    if analysis["bottlenecks"]:
        analysis["recommendations"].append(
            "Revoir la répartition des compétences ou ajuster les profils"
        )

    return analysis


def build_supervisor_planning(
    missions: List[Mission],
    result: OptimizationResult
) -> Dict[str, Dict[int, List[str]]]:
    """
    Construit le planning des superviseurs (validateurs), SÉPARÉ de celui des
    auditeurs exécutants.

    Un superviseur valide une (ou plusieurs) mission(s) ; son "planning" couvre
    les semaines actives des missions qu'il valide (il peut en superviser
    plusieurs en parallèle). Les missions où le superviseur participe aussi comme
    ressource sont marquées « (P) ».

    Returns:
        {nom_superviseur: {semaine: [libellés de missions]}}
    """
    # Semaines actives de chaque mission (d'après les allocations)
    mission_weeks: Dict[str, set] = {}
    for alloc in result.allocations + result.external_allocations:
        mission_weeks.setdefault(alloc.mission_name, set()).add(alloc.week)

    planning: Dict[str, Dict[int, List[str]]] = {}
    for mission in missions:
        sup = mission.supervisor_name
        if not sup:
            continue
        weeks = mission_weeks.get(mission.name, set())
        if not weeks:
            continue
        label = mission.name
        if getattr(mission, "supervisor_participates", False):
            label += " (P)"
        for w in sorted(weeks):
            planning.setdefault(sup, {}).setdefault(w, []).append(label)

    return planning


def calculate_supervisor_stats(
    missions: List[Mission],
    result: OptimizationResult
) -> Dict[str, Dict[str, Any]]:
    """
    Statistiques par superviseur (validateur) : nombre de missions validées,
    semaines de supervision, pic de missions simultanées, participations.

    Returns:
        {nom_superviseur: {missions, weeks, max_concurrent, participations}}
    """
    planning = build_supervisor_planning(missions, result)
    stats: Dict[str, Dict[str, Any]] = {}
    for sup, weeks_map in planning.items():
        all_missions = set()
        max_concurrent = 0
        participations = 0
        for w, labels in weeks_map.items():
            all_missions.update(lbl.replace(" (P)", "") for lbl in labels)
            max_concurrent = max(max_concurrent, len(labels))
        for m in missions:
            if m.supervisor_name == sup and getattr(m, "supervisor_participates", False):
                participations += 1
        stats[sup] = {
            "missions": len(all_missions),
            "weeks": len(weeks_map),
            "max_concurrent": max_concurrent,
            "participations": participations,
            "mission_names": sorted(all_missions),
        }
    return stats


def generate_gantt_data(
    result: OptimizationResult,
    missions: List[Mission],
    year: int = 2024
) -> List[Dict]:
    """
    Génère des données pour un diagramme de Gantt.

    Returns:
        Liste de dictionnaires avec les données pour chaque mission
    """
    gantt_data = []

    all_allocations = result.allocations + result.external_allocations

    # Grouper par mission
    mission_allocs = {}
    for alloc in all_allocations:
        if alloc.mission_name not in mission_allocs:
            mission_allocs[alloc.mission_name] = []
        mission_allocs[alloc.mission_name].append(alloc)

    for mission in missions:
        if mission.name in mission_allocs:
            allocs = mission_allocs[mission.name]
            weeks = [a.week for a in allocs]
            auditors = list(set(a.auditor_name for a in allocs))

            start_week = min(weeks)
            end_week = max(weeks)
            start_date, _ = week_to_date_range(start_week, year)
            _, end_date = week_to_date_range(end_week, year)

            gantt_data.append({
                "mission": mission.name,
                "start_week": start_week,
                "end_week": end_week,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "auditors": auditors,
                "supervisor": mission.supervisor_name,
                "duration_weeks": end_week - start_week + 1,
                "budget_days": mission.budget_days
            })
        else:
            gantt_data.append({
                "mission": mission.name,
                "start_week": None,
                "end_week": None,
                "start_date": None,
                "end_date": None,
                "auditors": [],
                "supervisor": mission.supervisor_name,
                "duration_weeks": 0,
                "budget_days": mission.budget_days,
                "status": "Non assigné"
            })

    return gantt_data
