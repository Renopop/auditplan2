"""
Modèles de données pour le système d'optimisation de staffing d'audit.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set, Dict
from datetime import date, datetime


class Skill(Enum):
    """Compétences possibles pour un auditeur."""
    IT = "IT"
    MODELE = "Modèle"
    ASSURANCE = "Assurance"
    GENERALISTE = "Généraliste"

    @classmethod
    def from_string(cls, value: str) -> Optional['Skill']:
        """Convertit une chaîne en Skill."""
        mapping = {
            'it': cls.IT,
            'modèle': cls.MODELE,
            'modele': cls.MODELE,
            'model': cls.MODELE,
            'assurance': cls.ASSURANCE,
            'généraliste': cls.GENERALISTE,
            'generaliste': cls.GENERALISTE,
            'general': cls.GENERALISTE,
        }
        return mapping.get(value.lower().strip())


class PeriodType(Enum):
    """Type de période pour une mission."""
    TRIMESTRE = "Trimestre"
    MOIS = "Mois"
    SEMAINE = "Semaine"


class AuditorGrade(Enum):
    """Grade des auditeurs."""
    JUNIOR = "Junior"
    CONFIRME = "Confirmé"
    SENIOR = "Senior"
    MANAGER = "Manager"

    @classmethod
    def from_string(cls, value: str) -> Optional['AuditorGrade']:
        """Convertit une chaîne en AuditorGrade."""
        if not value:
            return None
        mapping = {
            'junior': cls.JUNIOR,
            'confirmé': cls.CONFIRME,
            'confirme': cls.CONFIRME,
            'senior': cls.SENIOR,
            'manager': cls.MANAGER,
        }
        return mapping.get(value.lower().strip())


class AuditorStatus(Enum):
    """Statut d'un auditeur."""
    ACTIF = "Actif"
    FUTUR = "Futur"  # Arrivée prévue
    PARTI = "Parti"  # A quitté l'entreprise


class UnavailabilityType(Enum):
    """Type d'indisponibilité."""
    CONGE = "Congés"
    MALADIE = "Maladie"
    MATERNITE = "Maternité"
    PATERNITE = "Paternité"
    FORMATION = "Formation"
    AUTRE = "Autre"

    @classmethod
    def from_string(cls, value: str) -> Optional['UnavailabilityType']:
        """Convertit une chaîne en UnavailabilityType."""
        if not value:
            return None
        mapping = {
            'congés': cls.CONGE,
            'conges': cls.CONGE,
            'conge': cls.CONGE,
            'maladie': cls.MALADIE,
            'maternité': cls.MATERNITE,
            'maternite': cls.MATERNITE,
            'paternité': cls.PATERNITE,
            'paternite': cls.PATERNITE,
            'formation': cls.FORMATION,
            'autre': cls.AUTRE,
        }
        return mapping.get(value.lower().strip(), cls.AUTRE)


@dataclass
class Unavailability:
    """Représente une période d'indisponibilité d'un auditeur."""
    type: UnavailabilityType
    start_week: int
    end_week: int
    year: int = 2025
    reason: str = ""

    def get_weeks(self) -> Set[int]:
        """Retourne l'ensemble des semaines d'indisponibilité."""
        return set(range(self.start_week, self.end_week + 1))

    def to_dict(self) -> Dict:
        """Exporte en dictionnaire."""
        return {
            'type': self.type.value,
            'start_week': self.start_week,
            'end_week': self.end_week,
            'year': self.year,
            'reason': self.reason
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Unavailability':
        """Importe depuis un dictionnaire."""
        return cls(
            type=UnavailabilityType.from_string(data.get('type', 'Autre')) or UnavailabilityType.AUTRE,
            start_week=data.get('start_week', 1),
            end_week=data.get('end_week', 1),
            year=data.get('year', 2025),
            reason=data.get('reason', '')
        )


@dataclass
class AuditorProfile:
    """Profil complet d'un auditeur avec toutes ses informations."""
    name: str
    grade: AuditorGrade = AuditorGrade.CONFIRME
    budget_days: float = 205.0
    skills: Set[Skill] = field(default_factory=lambda: {Skill.GENERALISTE})
    certifications: List[str] = field(default_factory=list)

    # Dates de présence
    arrival_week: Optional[int] = None  # Semaine d'arrivée (None = déjà présent)
    arrival_year: Optional[int] = None
    departure_week: Optional[int] = None  # Semaine de départ (None = toujours présent)
    departure_year: Optional[int] = None

    # Indisponibilités
    unavailabilities: List[Unavailability] = field(default_factory=list)

    # Statut
    status: AuditorStatus = AuditorStatus.ACTIF

    # Peut superviser des missions
    is_supervisor: bool = False

    def __post_init__(self):
        if not self.skills:
            self.skills = {Skill.GENERALISTE}
        # Mettre à jour le statut automatiquement
        self._update_status()

    def _update_status(self, current_week: int = None, current_year: int = None):
        """Met à jour le statut en fonction des dates."""
        if current_week is None:
            current_week = datetime.now().isocalendar()[1]
        if current_year is None:
            current_year = datetime.now().year

        # Parti ?
        if self.departure_week and self.departure_year:
            if (self.departure_year < current_year or
                    (self.departure_year == current_year and self.departure_week < current_week)):
                self.status = AuditorStatus.PARTI
                return

        # Futur ?
        if self.arrival_week and self.arrival_year:
            if (self.arrival_year > current_year or
                    (self.arrival_year == current_year and self.arrival_week > current_week)):
                self.status = AuditorStatus.FUTUR
                return

        self.status = AuditorStatus.ACTIF

    def get_unavailable_weeks(self, year: int = 2025) -> Set[int]:
        """Retourne toutes les semaines d'indisponibilité pour une année donnée."""
        weeks = set()

        # Indisponibilités explicites
        for unavail in self.unavailabilities:
            if unavail.year == year:
                weeks.update(unavail.get_weeks())

        # Si l'auditeur arrive en cours d'année, il est indispo avant
        if self.arrival_week and self.arrival_year == year:
            weeks.update(range(1, self.arrival_week))

        # Si l'auditeur part en cours d'année, il est indispo après
        if self.departure_week and self.departure_year == year:
            weeks.update(range(self.departure_week + 1, 53))

        return weeks

    def is_available_week(self, week: int, year: int = 2025) -> bool:
        """Vérifie si l'auditeur est disponible une semaine donnée."""
        return week not in self.get_unavailable_weeks(year)

    def has_skill(self, skill: Skill) -> bool:
        """Vérifie si l'auditeur possède une compétence."""
        return skill in self.skills or Skill.GENERALISTE in self.skills

    def to_dict(self) -> Dict:
        """Exporte en dictionnaire pour JSON."""
        return {
            'name': self.name,
            'grade': self.grade.value,
            'budget_days': self.budget_days,
            'skills': [s.value for s in self.skills],
            'certifications': self.certifications,
            'arrival_week': self.arrival_week,
            'arrival_year': self.arrival_year,
            'departure_week': self.departure_week,
            'departure_year': self.departure_year,
            'unavailabilities': [u.to_dict() for u in self.unavailabilities],
            'status': self.status.value,
            'is_supervisor': self.is_supervisor
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'AuditorProfile':
        """Importe depuis un dictionnaire JSON."""
        skills = set()
        for s in data.get('skills', ['Généraliste']):
            skill = Skill.from_string(s)
            if skill:
                skills.add(skill)

        unavailabilities = [
            Unavailability.from_dict(u)
            for u in data.get('unavailabilities', [])
        ]

        grade = AuditorGrade.from_string(data.get('grade', 'Confirmé')) or AuditorGrade.CONFIRME
        status = AuditorStatus.ACTIF
        status_str = data.get('status', 'Actif')
        if status_str == 'Futur':
            status = AuditorStatus.FUTUR
        elif status_str == 'Parti':
            status = AuditorStatus.PARTI

        return cls(
            name=data['name'],
            grade=grade,
            budget_days=data.get('budget_days', 205.0),
            skills=skills if skills else {Skill.GENERALISTE},
            certifications=data.get('certifications', []),
            arrival_week=data.get('arrival_week'),
            arrival_year=data.get('arrival_year'),
            departure_week=data.get('departure_week'),
            departure_year=data.get('departure_year'),
            unavailabilities=unavailabilities,
            status=status,
            is_supervisor=data.get('is_supervisor', False)
        )

    def to_auditor(self, year: int = 2025) -> 'Auditor':
        """Convertit le profil en Auditor simple pour l'optimiseur."""
        return Auditor(
            name=self.name,
            budget_days=self.budget_days,
            skills=self.skills,
            unavailable_weeks=self.get_unavailable_weeks(year),
            is_external=False,
            is_supervisor=self.is_supervisor
        )


@dataclass
class Auditor:
    """Représente un auditeur avec ses compétences et disponibilités."""
    name: str
    budget_days: float  # Budget en jours homme par an
    skills: Set[Skill] = field(default_factory=set)
    unavailable_weeks: Set[int] = field(default_factory=set)  # Semaines d'indisponibilité (1-52)
    is_external: bool = False  # Ressource externe de backup
    is_supervisor: bool = False  # Peut être superviseur

    def __post_init__(self):
        if not self.skills:
            self.skills = {Skill.GENERALISTE}

    def is_available_week(self, week: int) -> bool:
        """Vérifie si l'auditeur est disponible une semaine donnée."""
        return week not in self.unavailable_weeks

    def has_skill(self, skill: Skill) -> bool:
        """Vérifie si l'auditeur possède une compétence."""
        return skill in self.skills or Skill.GENERALISTE in self.skills


@dataclass
class Mission:
    """Représente une mission d'audit."""
    name: str
    budget_days: float  # Budget en jours homme
    period_type: PeriodType = PeriodType.TRIMESTRE
    period_value: str = ""  # Ex: "Q1", "Janvier", "S12-S16"
    required_skills: Set[Skill] = field(default_factory=set)
    supervisor_name: Optional[str] = None  # Superviseur = VALIDATEUR (dernier relecteur)
    num_auditors: int = 1  # Nombre d'auditeurs nécessaires (équipe d'exécution)
    allowed_weeks: Set[int] = field(default_factory=set)  # Semaines autorisées
    # Le superviseur participe-t-il à la mission en tant que ressource ? (opt-in)
    supervisor_participates: bool = False
    # Chef de mission désigné (doit faire partie de l'équipe affectée). Optionnel.
    mission_lead_name: Optional[str] = None

    def __post_init__(self):
        if not self.required_skills:
            self.required_skills = {Skill.GENERALISTE}

    @property
    def days_per_auditor(self) -> float:
        """Calcule les jours par auditeur (approximatif)."""
        return self.budget_days / max(1, self.num_auditors)


@dataclass
class Allocation:
    """Représente une allocation auditeur-mission pour une semaine."""
    mission_name: str
    auditor_name: str
    week: int
    days: float  # Nombre de jours alloués cette semaine
    is_supervisor: bool = False


@dataclass
class OptimizationResult:
    """Résultat de l'optimisation."""
    success: bool
    allocations: List[Allocation] = field(default_factory=list)
    external_resources_needed: bool = False
    external_allocations: List[Allocation] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    unassigned_missions: List[str] = field(default_factory=list)

    def get_planning_matrix(self) -> Dict[str, Dict[int, List[str]]]:
        """
        Génère une matrice de planification.
        Retourne: {mission_name: {week: [auditor_names]}}
        """
        matrix = {}
        for alloc in self.allocations + self.external_allocations:
            if alloc.mission_name not in matrix:
                matrix[alloc.mission_name] = {}
            if alloc.week not in matrix[alloc.mission_name]:
                matrix[alloc.mission_name][alloc.week] = []

            name = alloc.auditor_name
            if alloc.is_supervisor:
                name += " (S)"
            if alloc not in self.allocations:
                name += " [EXT]"

            if name not in matrix[alloc.mission_name][alloc.week]:
                matrix[alloc.mission_name][alloc.week].append(name)

        return matrix

    def check_conflicts(self) -> List[str]:
        """
        Vérifie qu'aucun auditeur n'est sur 2 missions la même semaine.
        Retourne une liste des conflits trouvés.
        """
        conflicts = []
        auditor_week_mission = {}  # {(auditor, week): mission}

        for alloc in self.allocations + self.external_allocations:
            key = (alloc.auditor_name, alloc.week)
            if key in auditor_week_mission:
                existing_mission = auditor_week_mission[key]
                if existing_mission != alloc.mission_name:
                    conflicts.append(
                        f"{alloc.auditor_name} semaine {alloc.week}: "
                        f"{existing_mission} et {alloc.mission_name}"
                    )
            else:
                auditor_week_mission[key] = alloc.mission_name

        return conflicts

    def remove_conflicts(self) -> int:
        """
        Supprime les conflits en gardant la première allocation trouvée.
        Retourne le nombre de conflits supprimés.
        """
        auditor_week_mission = {}  # {(auditor, week): mission}
        clean_allocations = []
        clean_external = []
        removed = 0

        # D'abord les allocations internes (prioritaires)
        for alloc in self.allocations:
            key = (alloc.auditor_name, alloc.week)
            if key in auditor_week_mission:
                if auditor_week_mission[key] != alloc.mission_name:
                    removed += 1
                    continue  # Conflit, on ignore
            auditor_week_mission[key] = alloc.mission_name
            clean_allocations.append(alloc)

        # Ensuite les allocations externes
        for alloc in self.external_allocations:
            key = (alloc.auditor_name, alloc.week)
            if key in auditor_week_mission:
                if auditor_week_mission[key] != alloc.mission_name:
                    removed += 1
                    continue  # Conflit, on ignore
            auditor_week_mission[key] = alloc.mission_name
            clean_external.append(alloc)

        self.allocations = clean_allocations
        self.external_allocations = clean_external
        return removed

    def check_auditor_count(self, missions: List['Mission']) -> List[str]:
        """
        Vérifie que chaque mission a le bon nombre d'auditeurs par semaine.
        Retourne une liste des problèmes trouvés.
        """
        issues = []
        mission_dict = {m.name: m for m in missions}

        # Compter les auditeurs par mission et semaine
        mission_week_auditors = {}  # {(mission, week): set of auditors}
        for alloc in self.allocations + self.external_allocations:
            key = (alloc.mission_name, alloc.week)
            if key not in mission_week_auditors:
                mission_week_auditors[key] = set()
            mission_week_auditors[key].add(alloc.auditor_name)

        # Vérifier pour chaque mission
        for mission_name, mission in mission_dict.items():
            weeks_with_work = set(w for (m, w) in mission_week_auditors.keys() if m == mission_name)

            for week in weeks_with_work:
                auditors = mission_week_auditors.get((mission_name, week), set())
                count = len(auditors)
                if count != mission.num_auditors:
                    issues.append(
                        f"{mission_name} S{week}: {count} auditeurs (attendu: {mission.num_auditors})"
                    )

        return issues

    def check_gaps(self, blocked_weeks: Set[int] = None) -> List[str]:
        """
        Vérifie qu'il n'y a pas de trous dans les missions (bloc compact).
        Les semaines bloquées (août, décembre) ne comptent pas comme des trous.
        Retourne une liste des problèmes trouvés.
        """
        issues = []

        # Par défaut, utiliser les semaines bloquées standard
        if blocked_weeks is None:
            blocked_weeks = get_blocked_weeks()

        # Grouper les semaines par mission
        mission_weeks = {}  # {mission: set of weeks}
        for alloc in self.allocations + self.external_allocations:
            if alloc.mission_name not in mission_weeks:
                mission_weeks[alloc.mission_name] = set()
            mission_weeks[alloc.mission_name].add(alloc.week)

        for mission_name, weeks in mission_weeks.items():
            if len(weeks) < 2:
                continue

            sorted_weeks = sorted(weeks)
            for i in range(1, len(sorted_weeks)):
                w_prev = sorted_weeks[i-1]
                w_curr = sorted_weeks[i]

                # Compter le vrai gap (exclure les semaines bloquées)
                real_gap = 0
                for w in range(w_prev + 1, w_curr):
                    if w not in blocked_weeks:
                        real_gap += 1

                if real_gap > 0:
                    issues.append(
                        f"{mission_name}: trou de {real_gap} semaine(s) entre S{w_prev} et S{w_curr}"
                    )

        return issues

    def check_team_consistency(self) -> List[str]:
        """
        Vérifie que chaque auditeur travaille toutes les semaines de sa mission.
        Retourne une liste des problèmes trouvés.
        """
        issues = []

        # Grouper par mission: {mission: {auditor: set of weeks}}
        mission_auditor_weeks = {}
        mission_all_weeks = {}

        for alloc in self.allocations + self.external_allocations:
            m = alloc.mission_name
            a = alloc.auditor_name

            if m not in mission_auditor_weeks:
                mission_auditor_weeks[m] = {}
                mission_all_weeks[m] = set()

            if a not in mission_auditor_weeks[m]:
                mission_auditor_weeks[m][a] = set()

            mission_auditor_weeks[m][a].add(alloc.week)
            mission_all_weeks[m].add(alloc.week)

        for mission_name, auditor_weeks in mission_auditor_weeks.items():
            all_weeks = mission_all_weeks[mission_name]

            for auditor_name, weeks in auditor_weeks.items():
                missing = all_weeks - weeks
                if missing:
                    issues.append(
                        f"{mission_name}: {auditor_name} absent semaine(s) {sorted(missing)}"
                    )

        return issues

    def check_partial_weeks(self) -> List[str]:
        """
        Vérifie qu'il n'y a pas de semaines != 5 jours.
        Exception: dernière semaine d'une mission si reliquat.
        Retourne une liste des problèmes trouvés.
        """
        issues = []

        # Grouper par (mission, auditor, week) et sommer les jours
        allocation_days = {}  # {(mission, auditor, week): total_days}
        mission_weeks = {}  # {mission: set of weeks}

        for alloc in self.allocations + self.external_allocations:
            key = (alloc.mission_name, alloc.auditor_name, alloc.week)
            allocation_days[key] = allocation_days.get(key, 0) + alloc.days

            if alloc.mission_name not in mission_weeks:
                mission_weeks[alloc.mission_name] = set()
            mission_weeks[alloc.mission_name].add(alloc.week)

        # Trouver la dernière semaine de chaque mission
        last_week = {m: max(weeks) for m, weeks in mission_weeks.items() if weeks}

        # Vérifier chaque allocation
        for (mission, auditor, week), days in allocation_days.items():
            if days > 5:
                # Trop de jours = probablement des doublons d'allocations
                issues.append(
                    f"{mission} S{week}: {auditor} = {days:.1f}j (max: 5j)"
                )
            elif days < 5:
                is_last_week = (week == last_week.get(mission))
                if not is_last_week:
                    issues.append(
                        f"{mission} S{week}: {auditor} = {days:.1f}j (attendu: 5j)"
                    )

        return issues

    def check_budget(self, missions: List['Mission']) -> List[str]:
        """
        Vérifie que le budget alloué correspond au budget demandé pour chaque mission.
        Retourne une liste des problèmes trouvés.
        """
        issues = []
        mission_dict = {m.name: m for m in missions}

        # Calculer les jours alloués par mission
        allocated_days = {}
        for alloc in self.allocations + self.external_allocations:
            if alloc.mission_name not in allocated_days:
                allocated_days[alloc.mission_name] = 0
            allocated_days[alloc.mission_name] += alloc.days

        # Vérifier chaque mission
        for mission in missions:
            allocated = allocated_days.get(mission.name, 0)
            budget = mission.budget_days

            # Tolérance: arrondi au bloc de 5 jours * num_auditors
            block_size = 5 * mission.num_auditors
            budget_min = max(block_size, budget - block_size)
            budget_max = budget + block_size

            if allocated < budget_min:
                deficit = budget - allocated
                issues.append(
                    f"{mission.name}: {allocated:.0f}j alloués / {budget:.0f}j budget (manque {deficit:.0f}j)"
                )
            elif allocated > budget_max:
                excess = allocated - budget
                issues.append(
                    f"{mission.name}: {allocated:.0f}j alloués / {budget:.0f}j budget (excès {excess:.0f}j)"
                )

        return issues

    def deduplicate_allocations(self) -> int:
        """
        Supprime les doublons d'allocations (même mission/auditor/week).
        Retourne le nombre de doublons supprimés.
        """
        seen = set()
        clean_allocations = []
        duplicates = 0

        for alloc in self.allocations:
            key = (alloc.mission_name, alloc.auditor_name, alloc.week)
            if key not in seen:
                seen.add(key)
                clean_allocations.append(alloc)
            else:
                duplicates += 1

        self.allocations = clean_allocations

        # Même chose pour externes
        seen_ext = set()
        clean_external = []

        for alloc in self.external_allocations:
            key = (alloc.mission_name, alloc.auditor_name, alloc.week)
            if key not in seen_ext:
                seen_ext.add(key)
                clean_external.append(alloc)
            else:
                duplicates += 1

        self.external_allocations = clean_external
        return duplicates

    def to_dict(self) -> Dict:
        """Exporte le résultat en dictionnaire pour sauvegarde JSON."""
        return {
            'success': self.success,
            'allocations': [
                {
                    'mission_name': a.mission_name,
                    'auditor_name': a.auditor_name,
                    'week': a.week,
                    'days': a.days,
                    'is_supervisor': a.is_supervisor
                }
                for a in self.allocations
            ],
            'external_allocations': [
                {
                    'mission_name': a.mission_name,
                    'auditor_name': a.auditor_name,
                    'week': a.week,
                    'days': a.days,
                    'is_supervisor': a.is_supervisor
                }
                for a in self.external_allocations
            ],
            'external_resources_needed': self.external_resources_needed,
            'messages': self.messages,
            'unassigned_missions': self.unassigned_missions
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'OptimizationResult':
        """Importe un résultat depuis un dictionnaire JSON."""
        result = cls(success=data.get('success', False))
        result.allocations = [
            Allocation(
                mission_name=a['mission_name'],
                auditor_name=a['auditor_name'],
                week=a['week'],
                days=a['days'],
                is_supervisor=a.get('is_supervisor', False)
            )
            for a in data.get('allocations', [])
        ]
        result.external_allocations = [
            Allocation(
                mission_name=a['mission_name'],
                auditor_name=a['auditor_name'],
                week=a['week'],
                days=a['days'],
                is_supervisor=a.get('is_supervisor', False)
            )
            for a in data.get('external_allocations', [])
        ]
        result.external_resources_needed = data.get('external_resources_needed', False)
        result.messages = data.get('messages', [])
        result.unassigned_missions = data.get('unassigned_missions', [])
        return result


@dataclass
class PlanningConfig:
    """Configuration globale de la planification."""
    year: int = 2024
    blocked_weeks: Set[int] = field(default_factory=set)  # Semaines bloquées globalement
    external_resource_budget: float = 205.0  # Budget ressource externe de backup

    def __post_init__(self):
        # Calcul des semaines bloquées par défaut (août + 15-30 décembre)
        if not self.blocked_weeks:
            self.blocked_weeks = self._calculate_blocked_weeks()

    def _calculate_blocked_weeks(self) -> Set[int]:
        """Calcule les semaines bloquées (août + 15-30 décembre)."""
        blocked = set()

        # Semaines d'août (environ semaines 31-35)
        # Pour 2024: août commence semaine 31
        for week in range(31, 36):
            blocked.add(week)

        # Semaines du 15-30 décembre (environ semaines 51-52)
        blocked.add(51)
        blocked.add(52)

        return blocked

    def is_week_available(self, week: int) -> bool:
        """Vérifie si une semaine est disponible pour les missions."""
        return week not in self.blocked_weeks


def get_blocked_weeks(year: int = 2024) -> Set[int]:
    """Retourne les semaines bloquées (août S31-35 + décembre S51-52)."""
    return {31, 32, 33, 34, 35, 51, 52}


def get_weeks_for_period(period_type: PeriodType, period_value: str, year: int = 2024, exclude_blocked: bool = True) -> Set[int]:
    """
    Convertit une période en ensemble de semaines.

    Args:
        period_type: Type de période (Trimestre, Mois, Semaine)
        period_value: Valeur de la période (ex: "Q1", "Janvier", "S12-S16")
        year: Année de référence
        exclude_blocked: Si True, exclut automatiquement les semaines bloquées (août/décembre)

    Returns:
        Ensemble des numéros de semaines (sans les semaines bloquées si exclude_blocked=True)
    """
    weeks = set()
    blocked_weeks = get_blocked_weeks(year) if exclude_blocked else set()

    if period_type == PeriodType.TRIMESTRE:
        quarter_map = {
            'q1': range(1, 14),
            't1': range(1, 14),
            '1': range(1, 14),
            'q2': range(14, 27),
            't2': range(14, 27),
            '2': range(14, 27),
            'q3': range(27, 40),
            't3': range(27, 40),
            '3': range(27, 40),
            'q4': range(40, 53),
            't4': range(40, 53),
            '4': range(40, 53),
        }
        key = period_value.lower().strip()
        if key in quarter_map:
            weeks = set(quarter_map[key])

    elif period_type == PeriodType.MOIS:
        month_map = {
            'janvier': range(1, 5),
            'jan': range(1, 5),
            '1': range(1, 5),
            'février': range(5, 9),
            'fevrier': range(5, 9),
            'feb': range(5, 9),
            '2': range(5, 9),
            'mars': range(9, 14),
            'mar': range(9, 14),
            '3': range(9, 14),
            'avril': range(14, 18),
            'avr': range(14, 18),
            '4': range(14, 18),
            'mai': range(18, 22),
            'may': range(18, 22),
            '5': range(18, 22),
            'juin': range(22, 27),
            'jun': range(22, 27),
            '6': range(22, 27),
            'juillet': range(27, 31),
            'jul': range(27, 31),
            '7': range(27, 31),
            'août': range(31, 36),
            'aout': range(31, 36),
            'aug': range(31, 36),
            '8': range(31, 36),
            'septembre': range(36, 40),
            'sep': range(36, 40),
            '9': range(36, 40),
            'octobre': range(40, 44),
            'oct': range(40, 44),
            '10': range(40, 44),
            'novembre': range(44, 49),
            'nov': range(44, 49),
            '11': range(44, 49),
            'décembre': range(49, 53),
            'decembre': range(49, 53),
            'dec': range(49, 53),
            '12': range(49, 53),
        }
        # Gère les mois multiples séparés par virgule ou tiret
        period_clean = period_value.lower().strip()
        if ',' in period_clean:
            for part in period_clean.split(','):
                part = part.strip()
                if part in month_map:
                    weeks.update(month_map[part])
        elif '-' in period_clean and not period_clean.startswith('s'):
            parts = period_clean.split('-')
            if len(parts) == 2:
                # Range de mois
                start_month = parts[0].strip()
                end_month = parts[1].strip()
                if start_month in month_map and end_month in month_map:
                    start_week = min(month_map[start_month])
                    end_week = max(month_map[end_month])
                    weeks = set(range(start_week, end_week + 1))
        else:
            if period_clean in month_map:
                weeks = set(month_map[period_clean])

    elif period_type == PeriodType.SEMAINE:
        period_clean = period_value.lower().strip()
        # Format: S12, S12-S16, 12-16, etc.
        period_clean = period_clean.replace('s', '').replace('w', '')
        if '-' in period_clean:
            parts = period_clean.split('-')
            if len(parts) == 2:
                try:
                    start = int(parts[0].strip())
                    end = int(parts[1].strip())
                    weeks = set(range(start, end + 1))
                except ValueError:
                    pass
        else:
            try:
                week = int(period_clean)
                weeks = {week}
            except ValueError:
                pass

    # Exclure les semaines bloquées
    return weeks - blocked_weeks
