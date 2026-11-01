"""
Gestion des contraintes pour l'optimisation du staffing d'audit.
"""

from datetime import date, timedelta
from typing import List, Set, Dict, Tuple
from models import Auditor, Mission, PlanningConfig, Skill


class ConstraintManager:
    """Gestionnaire des contraintes de planification."""

    def __init__(self, config: PlanningConfig):
        self.config = config

    def get_blocked_weeks_for_year(self, year: int) -> Set[int]:
        """
        Calcule les semaines bloquées pour une année donnée.
        - Août entier (semaines ~31-35)
        - 15-30 décembre (semaines ~51-52)
        """
        blocked = set()

        # Trouver les semaines d'août
        august_start = date(year, 8, 1)
        august_end = date(year, 8, 31)

        current = august_start
        while current <= august_end:
            blocked.add(current.isocalendar()[1])
            current += timedelta(days=7)

        # Trouver les semaines du 15-30 décembre
        dec_15 = date(year, 12, 15)
        dec_31 = date(year, 12, 31)

        current = dec_15
        while current <= dec_31:
            week = current.isocalendar()[1]
            # Gérer le cas où la semaine 1 de l'année suivante commence fin décembre
            if week > 50 or week == 1:
                if week > 50:
                    blocked.add(week)
            current += timedelta(days=1)

        return blocked

    def validate_mission_period(self, mission: Mission) -> Tuple[bool, str]:
        """
        Valide que la période d'une mission ne tombe pas sur des semaines bloquées.

        Returns:
            Tuple (is_valid, message)
        """
        blocked = self.config.blocked_weeks
        mission_weeks = mission.allowed_weeks

        if not mission_weeks:
            return False, f"Mission '{mission.name}' n'a aucune semaine autorisée"

        # Semaines disponibles = semaines autorisées - semaines bloquées
        available_weeks = mission_weeks - blocked

        if not available_weeks:
            # Toutes les semaines sont bloquées -> erreur
            return False, f"Mission '{mission.name}' n'a que des semaines bloquées (août/décembre)"

        overlap = mission_weeks & blocked
        if overlap:
            # Certaines semaines sont bloquées mais pas toutes -> warning
            return True, f"Mission '{mission.name}': semaines {sorted(overlap)} exclues (bloquées)"

        return True, "OK"

    def validate_auditor_availability(
        self, auditor: Auditor, mission: Mission
    ) -> Tuple[bool, Set[int]]:
        """
        Vérifie la disponibilité d'un auditeur pour une mission.

        Returns:
            Tuple (has_availability, available_weeks)
        """
        mission_weeks = mission.allowed_weeks
        auditor_unavailable = auditor.unavailable_weeks
        blocked = self.config.blocked_weeks

        # Semaines disponibles = semaines mission - indisponibilités - bloquées
        available = mission_weeks - auditor_unavailable - blocked

        return len(available) > 0, available

    def check_skill_match(self, auditor: Auditor, mission: Mission) -> float:
        """
        Calcule un score de correspondance des compétences.
        IMPORTANT: Généraliste est une compétence spécifique, PAS un joker.

        Returns:
            Score entre 0 et 1 (1 = correspondance parfaite)
        """
        if not mission.required_skills:
            return 1.0

        # Généraliste n'est PAS un joker - c'est une compétence comme les autres
        # On calcule simplement l'intersection des compétences
        matching = auditor.skills & mission.required_skills
        if not matching:
            return 0.0

        return len(matching) / len(mission.required_skills)

    def calculate_workload(
        self, auditor: Auditor, existing_allocations: Dict[int, float]
    ) -> Dict[str, float]:
        """
        Calcule la charge de travail d'un auditeur.

        Args:
            auditor: L'auditeur
            existing_allocations: {semaine: jours_alloués}

        Returns:
            Dictionnaire avec statistiques de charge
        """
        total_allocated = sum(existing_allocations.values())
        remaining = auditor.budget_days - total_allocated

        return {
            "total_allocated": total_allocated,
            "remaining_days": remaining,
            "utilization_rate": total_allocated / auditor.budget_days if auditor.budget_days > 0 else 0,
            "weeks_assigned": len(existing_allocations),
        }

    def get_feasible_weeks_for_mission(
        self, mission: Mission, auditors: List[Auditor]
    ) -> Set[int]:
        """
        Trouve les semaines où la mission peut être exécutée avec au moins un auditeur.
        """
        feasible = set()
        blocked = self.config.blocked_weeks

        for week in mission.allowed_weeks:
            if week in blocked:
                continue

            # Vérifier si au moins num_auditors sont disponibles
            available_auditors = [
                a for a in auditors
                if week not in a.unavailable_weeks and self.check_skill_match(a, mission) > 0
            ]

            if len(available_auditors) >= mission.num_auditors:
                feasible.add(week)

        return feasible


class ConstraintValidator:
    """Validateur global des contraintes."""

    def __init__(self, config: PlanningConfig):
        self.config = config
        self.manager = ConstraintManager(config)
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_all(
        self, missions: List[Mission], auditors: List[Auditor]
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Valide toutes les contraintes.

        Returns:
            Tuple (is_valid, errors, warnings)
        """
        self.errors = []
        self.warnings = []

        self._validate_missions(missions)
        self._validate_auditors(auditors)
        self._validate_capacity(missions, auditors)

        return len(self.errors) == 0, self.errors, self.warnings

    def _validate_missions(self, missions: List[Mission]):
        """Valide les missions."""
        for mission in missions:
            # Vérifier période
            is_valid, msg = self.manager.validate_mission_period(mission)
            if not is_valid:
                self.warnings.append(msg)

            # Vérifier budget
            if mission.budget_days <= 0:
                self.errors.append(f"Mission '{mission.name}' a un budget invalide: {mission.budget_days}")

            # Vérifier nombre d'auditeurs
            if mission.num_auditors <= 0:
                self.errors.append(f"Mission '{mission.name}' a un nombre d'auditeurs invalide")

    def _validate_auditors(self, auditors: List[Auditor]):
        """Valide les auditeurs."""
        names = set()
        for auditor in auditors:
            if auditor.name in names:
                self.errors.append(f"Auditeur en doublon: '{auditor.name}'")
            names.add(auditor.name)

            if auditor.budget_days <= 0:
                self.warnings.append(f"Auditeur '{auditor.name}' a un budget nul ou négatif")

    def _validate_capacity(self, missions: List[Mission], auditors: List[Auditor]):
        """Valide la capacité globale."""
        total_mission_days = sum(m.budget_days for m in missions)
        total_auditor_days = sum(a.budget_days for a in auditors if not a.is_external)

        if total_mission_days > total_auditor_days:
            deficit = total_mission_days - total_auditor_days
            self.warnings.append(
                f"Capacité insuffisante: {total_mission_days:.0f}j requis, "
                f"{total_auditor_days:.0f}j disponibles (déficit: {deficit:.0f}j)"
            )

def parse_unavailable_periods(period_string: str, year: int = 2024) -> Set[int]:
    """
    Parse une chaîne de périodes d'indisponibilité en semaines.

    Formats supportés:
    - "S12-S16" ou "12-16" : semaines 12 à 16
    - "août" ou "aout" : mois d'août
    - "15/08-30/08" : dates spécifiques
    - "S12, S15, S20" : semaines individuelles

    Returns:
        Ensemble des numéros de semaines
    """
    weeks = set()
    if not period_string or period_string.strip() == "":
        return weeks

    period_string = period_string.lower().strip()

    # Séparateur virgule pour plusieurs périodes
    parts = [p.strip() for p in period_string.split(',')]

    for part in parts:
        # Format semaine: S12 ou 12
        if part.startswith('s') or part.isdigit():
            clean = part.replace('s', '').replace('w', '')
            if '-' in clean:
                try:
                    start, end = clean.split('-')
                    weeks.update(range(int(start), int(end) + 1))
                except ValueError:
                    pass
            else:
                try:
                    weeks.add(int(clean))
                except ValueError:
                    pass

        # Format date: JJ/MM-JJ/MM
        elif '/' in part:
            try:
                if '-' in part:
                    start_str, end_str = part.split('-')
                    start_day, start_month = map(int, start_str.split('/'))
                    end_day, end_month = map(int, end_str.split('/'))

                    start_date = date(year, start_month, start_day)
                    end_date = date(year, end_month, end_day)

                    current = start_date
                    while current <= end_date:
                        weeks.add(current.isocalendar()[1])
                        current += timedelta(days=1)
                else:
                    day, month = map(int, part.split('/'))
                    d = date(year, month, day)
                    weeks.add(d.isocalendar()[1])
            except (ValueError, IndexError):
                pass

        # Format mois
        else:
            month_weeks = {
                'janvier': range(1, 5),
                'jan': range(1, 5),
                'février': range(5, 9),
                'fevrier': range(5, 9),
                'mars': range(9, 14),
                'avril': range(14, 18),
                'mai': range(18, 22),
                'juin': range(22, 27),
                'juillet': range(27, 31),
                'août': range(31, 36),
                'aout': range(31, 36),
                'septembre': range(36, 40),
                'octobre': range(40, 44),
                'novembre': range(44, 49),
                'décembre': range(49, 53),
                'decembre': range(49, 53),
            }
            if part in month_weeks:
                weeks.update(month_weeks[part])

    return weeks
