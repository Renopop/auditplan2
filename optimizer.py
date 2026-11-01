"""
Moteur d'optimisation haute performance pour le staffing des missions d'audit.
Approche hybride: Heuristique de construction + Amélioration locale + MILP raffinement.
"""

from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict
from dataclasses import dataclass, field
import random
import time
import os

from models import (
    Auditor, Mission, Allocation, OptimizationResult,
    PlanningConfig, Skill
)
from constraints import ConstraintManager, ConstraintValidator


@dataclass
class AuditorState:
    """État d'un auditeur pendant l'optimisation."""
    name: str
    budget_remaining: float
    skills: Set[Skill]
    is_external: bool
    unavailable_weeks: Set[int]
    assigned_weeks: Dict[int, str] = field(default_factory=dict)

    def can_work_week(self, week: int, blocked_weeks: Set[int]) -> bool:
        return (week not in self.unavailable_weeks and
                week not in blocked_weeks and
                week not in self.assigned_weeks)

    def days_used(self) -> float:
        return len(self.assigned_weeks) * 5.0


@dataclass
class MissionState:
    """État d'une mission pendant l'optimisation."""
    name: str
    budget_days: float
    num_auditors: int
    required_skills: Set[Skill]
    allowed_weeks: Set[int]
    supervisor_name: Optional[str]
    assigned_auditors: Set[str] = field(default_factory=set)
    active_weeks: Set[int] = field(default_factory=set)

    def days_allocated(self) -> float:
        return len(self.active_weeks) * self.num_auditors * 5.0

    def weeks_needed(self) -> int:
        return max(1, int(self.budget_days / (self.num_auditors * 5.0) + 0.99))

    def is_complete(self) -> bool:
        return self.days_allocated() >= self.budget_days * 0.95


class FastAuditOptimizer:
    """
    Optimiseur haute performance utilisant une approche heuristique.

    Stratégie:
    1. Trier les missions par difficulté (compétences rares, superviseur, budget)
    2. Construction gloutonne: assigner les missions une par une
    3. Amélioration locale: échanger/déplacer pour améliorer
    4. Fallback MILP si nécessaire (uniquement pour sous-ensemble problématique)
    """

    DAYS_PER_WEEK = 5.0

    def __init__(self, config: PlanningConfig):
        self.config = config
        self.constraint_manager = ConstraintManager(config)
        self.progress_info = {
            'status': 'Initialisation...',
            'phase': 'init',
            'progress': 0
        }

    def optimize(
        self,
        missions: List[Mission],
        auditors: List[Auditor],
        verbose: bool = False,
        time_limit: int = 60,
        gap_tolerance: float = 0.02,
        max_subcontracting_days: Optional[float] = None
    ) -> OptimizationResult:
        """
        Lance l'optimisation rapide du staffing.
        """
        start_time = time.time()

        # Validation
        validator = ConstraintValidator(self.config)
        is_valid, errors, warnings = validator.validate_all(missions, auditors)

        result = OptimizationResult(success=False, messages=warnings.copy())
        if errors:
            result.messages = errors + result.messages
            return result

        # Préparer les états
        internal_auditors = [a for a in auditors if not a.is_external]
        auditor_states = {a.name: self._create_auditor_state(a) for a in internal_auditors}
        mission_states = {m.name: self._create_mission_state(m) for m in missions}

        # Phase 1: Construction gloutonne
        self.progress_info['phase'] = 'construction'
        self.progress_info['status'] = 'Construction initiale...'

        sorted_missions = self._sort_missions_by_difficulty(missions, internal_auditors)

        for i, mission in enumerate(sorted_missions):
            self.progress_info['progress'] = int(100 * i / len(missions))
            ms = mission_states[mission.name]
            self._assign_mission_greedy(ms, auditor_states, verbose)

        # Phase 2: Compléter avec sous-traitants si nécessaire
        self.progress_info['phase'] = 'completion'
        self.progress_info['status'] = 'Vérification couverture...'

        incomplete_missions = [ms for ms in mission_states.values() if not ms.is_complete()]
        external_states = {}

        if incomplete_missions:
            if verbose:
                print(f"  {len(incomplete_missions)} missions incomplètes, ajout sous-traitants...")

            # Créer sous-traitants à la demande
            ext_idx = 1
            for ms in incomplete_missions:
                weeks_missing = ms.weeks_needed() - len(ms.active_weeks)
                auditors_missing = ms.num_auditors - len(ms.assigned_auditors)

                if auditors_missing > 0 or weeks_missing > 0:
                    # Trouver des semaines disponibles
                    available_weeks = sorted(ms.allowed_weeks - self.config.blocked_weeks - ms.active_weeks)

                    for _ in range(auditors_missing):
                        ext_name = f"Sous-traitant_{ext_idx}"
                        ext_idx += 1
                        ext_state = AuditorState(
                            name=ext_name,
                            budget_remaining=260,
                            skills={Skill.IT, Skill.MODELE, Skill.ASSURANCE, Skill.GENERALISTE},
                            is_external=True,
                            unavailable_weeks=set()
                        )
                        external_states[ext_name] = ext_state
                        ms.assigned_auditors.add(ext_name)

                    # Étendre les semaines actives
                    for w in available_weeks[:weeks_missing]:
                        if self._can_activate_week(ms, w, auditor_states, external_states):
                            ms.active_weeks.add(w)
                            for a_name in ms.assigned_auditors:
                                if a_name in auditor_states:
                                    auditor_states[a_name].assigned_weeks[w] = ms.name
                                elif a_name in external_states:
                                    external_states[a_name].assigned_weeks[w] = ms.name

        # Phase 3: Amélioration locale (optionnelle, si temps restant)
        elapsed = time.time() - start_time
        if elapsed < time_limit * 0.8:
            self.progress_info['phase'] = 'improvement'
            self.progress_info['status'] = 'Amélioration locale...'
            self._local_improvement(mission_states, auditor_states, external_states,
                                   time_limit - elapsed, verbose)

        # Construire le résultat
        self.progress_info['phase'] = 'finalization'
        self.progress_info['status'] = 'Finalisation...'

        allocations = []
        external_allocations = []

        for ms in mission_states.values():
            mission_obj = next(m for m in missions if m.name == ms.name)
            for week in sorted(ms.active_weeks):
                for auditor_name in ms.assigned_auditors:
                    is_supervisor = (auditor_name == ms.supervisor_name)
                    alloc = Allocation(
                        mission_name=ms.name,
                        auditor_name=auditor_name,
                        week=week,
                        days=self.DAYS_PER_WEEK,
                        is_supervisor=is_supervisor
                    )
                    if auditor_name in external_states:
                        external_allocations.append(alloc)
                    else:
                        allocations.append(alloc)

        result.success = True
        result.allocations = allocations
        result.external_allocations = external_allocations
        result.external_resources_needed = len(external_allocations) > 0

        # Messages de résumé
        elapsed = time.time() - start_time
        result.messages.append(f"Optimisation terminée en {elapsed:.1f}s")

        if external_allocations:
            ext_days = sum(a.days for a in external_allocations)
            ext_names = set(a.auditor_name for a in external_allocations)
            result.messages.append(f"Sous-traitance: {ext_days:.0f}j ({len(ext_names)} ressources)")
        else:
            result.messages.append("Solution 100% interne")

        # Vérifier missions incomplètes
        incomplete = [ms.name for ms in mission_states.values() if not ms.is_complete()]
        if incomplete:
            result.messages.append(f"Attention: {len(incomplete)} mission(s) partiellement staffées")
            result.unassigned_missions = incomplete

        self.progress_info['status'] = 'Terminé'
        self.progress_info['progress'] = 100

        return result

    def _create_auditor_state(self, auditor: Auditor) -> AuditorState:
        return AuditorState(
            name=auditor.name,
            budget_remaining=auditor.budget_days,
            skills=auditor.skills.copy(),
            is_external=auditor.is_external,
            unavailable_weeks=auditor.unavailable_weeks.copy()
        )

    def _create_mission_state(self, mission: Mission) -> MissionState:
        return MissionState(
            name=mission.name,
            budget_days=mission.budget_days,
            num_auditors=mission.num_auditors,
            required_skills=mission.required_skills.copy(),
            allowed_weeks=mission.allowed_weeks - self.config.blocked_weeks,
            supervisor_name=mission.supervisor_name
        )

    def _sort_missions_by_difficulty(
        self, missions: List[Mission], auditors: List[Auditor]
    ) -> List[Mission]:
        """
        Trie les missions par difficulté décroissante.
        Missions difficiles en premier (compétences rares, contraintes fortes).
        """
        def difficulty_score(m: Mission) -> Tuple:
            # Nombre d'auditeurs avec les compétences requises
            matching_auditors = sum(
                1 for a in auditors
                if m.required_skills & a.skills or Skill.GENERALISTE in a.skills
            )

            # Semaines disponibles
            available_weeks = len(m.allowed_weeks - self.config.blocked_weeks)

            # Score de difficulté (plus élevé = plus difficile)
            skill_scarcity = -matching_auditors
            time_constraint = -available_weeks
            size = m.budget_days
            has_supervisor = 1 if m.supervisor_name else 0

            return (has_supervisor, skill_scarcity, time_constraint, -size)

        return sorted(missions, key=difficulty_score, reverse=True)

    def _skill_match_score(self, auditor_skills: Set[Skill], required_skills: Set[Skill]) -> float:
        """Calcule le score de correspondance des compétences."""
        if not required_skills:
            return 1.0
        if Skill.GENERALISTE in auditor_skills:
            return 0.7
        matching = auditor_skills & required_skills
        return len(matching) / len(required_skills) if required_skills else 1.0

    def _assign_mission_greedy(
        self,
        mission: MissionState,
        auditor_states: Dict[str, AuditorState],
        verbose: bool = False
    ) -> bool:
        """
        Assigne une mission de manière gloutonne.
        Retourne True si succès complet.
        """
        # Trouver les semaines possibles
        available_weeks = sorted(mission.allowed_weeks)
        if not available_weeks:
            return False

        weeks_needed = mission.weeks_needed()

        # Trouver les auditeurs éligibles
        eligible_auditors = []
        for name, state in auditor_states.items():
            score = self._skill_match_score(state.skills, mission.required_skills)
            if score > 0:
                # Calculer disponibilité
                avail_weeks = sum(1 for w in available_weeks if state.can_work_week(w, self.config.blocked_weeks))
                if avail_weeks >= weeks_needed:
                    eligible_auditors.append((name, score, avail_weeks))

        # Trier par score puis disponibilité
        eligible_auditors.sort(key=lambda x: (-x[1], -x[2]))

        # Superviseur en priorité
        if mission.supervisor_name:
            supervisor_idx = next(
                (i for i, (n, _, _) in enumerate(eligible_auditors) if n == mission.supervisor_name),
                None
            )
            if supervisor_idx is not None:
                eligible_auditors.insert(0, eligible_auditors.pop(supervisor_idx))

        # Sélectionner les auditeurs
        selected = []
        for name, score, _ in eligible_auditors:
            if len(selected) >= mission.num_auditors:
                break
            selected.append(name)

        if len(selected) < mission.num_auditors:
            # Pas assez d'auditeurs internes
            return False

        mission.assigned_auditors = set(selected)

        # Trouver le meilleur bloc de semaines consécutives
        best_start = self._find_best_week_block(
            mission, selected, auditor_states, weeks_needed
        )

        if best_start is None:
            return False

        # Assigner les semaines
        weeks_to_assign = sorted(available_weeks)
        start_idx = weeks_to_assign.index(best_start) if best_start in weeks_to_assign else 0

        assigned_weeks = []
        for i in range(start_idx, min(start_idx + weeks_needed, len(weeks_to_assign))):
            w = weeks_to_assign[i]
            if all(auditor_states[a].can_work_week(w, self.config.blocked_weeks) for a in selected):
                assigned_weeks.append(w)
                for a in selected:
                    auditor_states[a].assigned_weeks[w] = mission.name

        mission.active_weeks = set(assigned_weeks)

        return len(assigned_weeks) >= weeks_needed

    def _find_best_week_block(
        self,
        mission: MissionState,
        auditors: List[str],
        auditor_states: Dict[str, AuditorState],
        weeks_needed: int
    ) -> Optional[int]:
        """Trouve le meilleur bloc de semaines consécutives."""
        available_weeks = sorted(mission.allowed_weeks)
        best_start = None
        best_score = -1

        for i in range(len(available_weeks) - weeks_needed + 1):
            candidate_weeks = available_weeks[i:i + weeks_needed]

            # Vérifier que tous les auditeurs sont disponibles
            all_available = all(
                auditor_states[a].can_work_week(w, self.config.blocked_weeks)
                for w in candidate_weeks
                for a in auditors
            )

            if all_available:
                # Score: préférer les blocs plus tôt dans l'année
                score = 100 - candidate_weeks[0]
                if score > best_score:
                    best_score = score
                    best_start = candidate_weeks[0]

        return best_start

    def _can_activate_week(
        self,
        mission: MissionState,
        week: int,
        auditor_states: Dict[str, AuditorState],
        external_states: Dict[str, AuditorState]
    ) -> bool:
        """Vérifie si une semaine peut être activée pour une mission."""
        if week not in mission.allowed_weeks or week in self.config.blocked_weeks:
            return False

        for a_name in mission.assigned_auditors:
            state = auditor_states.get(a_name) or external_states.get(a_name)
            if state and not state.can_work_week(week, self.config.blocked_weeks):
                return False

        return True

    def _local_improvement(
        self,
        mission_states: Dict[str, MissionState],
        auditor_states: Dict[str, AuditorState],
        external_states: Dict[str, AuditorState],
        time_budget: float,
        verbose: bool = False
    ):
        """
        Amélioration locale: réduire la sous-traitance si possible.
        """
        start_time = time.time()
        improvements = 0

        # Missions utilisant des sous-traitants
        ext_missions = [
            ms for ms in mission_states.values()
            if any(a in external_states for a in ms.assigned_auditors)
        ]

        for ms in ext_missions:
            if time.time() - start_time > time_budget:
                break

            # Essayer de remplacer sous-traitants par internes
            ext_auditors = [a for a in ms.assigned_auditors if a in external_states]

            for ext_name in ext_auditors:
                # Chercher un interne disponible
                for int_name, int_state in auditor_states.items():
                    if int_name in ms.assigned_auditors:
                        continue

                    # Vérifier compétences
                    if self._skill_match_score(int_state.skills, ms.required_skills) == 0:
                        continue

                    # Vérifier disponibilité sur toutes les semaines actives
                    can_replace = all(
                        int_state.can_work_week(w, self.config.blocked_weeks)
                        for w in ms.active_weeks
                    )

                    if can_replace:
                        # Échanger
                        ms.assigned_auditors.remove(ext_name)
                        ms.assigned_auditors.add(int_name)

                        # Mettre à jour les semaines
                        for w in ms.active_weeks:
                            if w in external_states[ext_name].assigned_weeks:
                                del external_states[ext_name].assigned_weeks[w]
                            int_state.assigned_weeks[w] = ms.name

                        improvements += 1
                        break

        if verbose and improvements > 0:
            print(f"  Amélioration locale: {improvements} remplacements")


class AuditStaffingOptimizer:
    """
    Interface principale d'optimisation.

    Moteur par défaut : CP-SAT (OR-Tools) — exact, rapide et précis.
    Repli automatique sur l'heuristique gloutonne si OR-Tools n'est pas installé.
    """

    DAYS_PER_WEEK = 5.0

    def __init__(self, config: PlanningConfig):
        self.config = config
        self.constraint_manager = ConstraintManager(config)
        self._fast_optimizer = FastAuditOptimizer(config)

        # Moteur exact CP-SAT si disponible
        self._cp_optimizer = None
        try:
            from cp_optimizer import CPAuditOptimizer, ORTOOLS_AVAILABLE
            if ORTOOLS_AVAILABLE:
                self._cp_optimizer = CPAuditOptimizer(config)
        except Exception:
            self._cp_optimizer = None

        # progress_info pointe vers le moteur actif (pour l'affichage temps réel)
        self.progress_info = (
            self._cp_optimizer.progress_info
            if self._cp_optimizer else self._fast_optimizer.progress_info
        )

    def optimize(
        self,
        missions: List[Mission],
        auditors: List[Auditor],
        verbose: bool = False,
        time_limit: int = 60,
        gap_tolerance: float = 0.02,
        max_subcontracting_days: Optional[float] = None,
        min_load_ratio: float = 0.80
    ) -> OptimizationResult:
        """
        Lance l'optimisation du staffing.

        Utilise CP-SAT (optimal) si disponible, sinon l'heuristique rapide.
        """
        if self._cp_optimizer is not None:
            self.progress_info = self._cp_optimizer.progress_info
            return self._cp_optimizer.optimize(
                missions, auditors, verbose=verbose,
                time_limit=time_limit, gap_tolerance=gap_tolerance,
                max_subcontracting_days=max_subcontracting_days,
                min_load_ratio=min_load_ratio,
            )

        # Repli : heuristique gloutonne
        self.progress_info = self._fast_optimizer.progress_info
        return self._fast_optimizer.optimize(
            missions, auditors, verbose, time_limit, gap_tolerance, max_subcontracting_days
        )

    def get_planning_summary(self, result: OptimizationResult) -> Dict:
        """Génère un résumé de la planification."""
        if not result.success:
            return {"success": False, "message": "Optimisation échouée"}

        all_allocations = result.allocations + result.external_allocations

        mission_stats = defaultdict(lambda: {"auditors": set(), "weeks": set(), "total_days": 0})
        for alloc in all_allocations:
            mission_stats[alloc.mission_name]["auditors"].add(alloc.auditor_name)
            mission_stats[alloc.mission_name]["weeks"].add(alloc.week)
            mission_stats[alloc.mission_name]["total_days"] += alloc.days

        auditor_stats = defaultdict(lambda: {"missions": set(), "total_days": 0})
        for alloc in all_allocations:
            auditor_stats[alloc.auditor_name]["missions"].add(alloc.mission_name)
            auditor_stats[alloc.auditor_name]["total_days"] += alloc.days

        return {
            "success": True,
            "total_allocations": len(all_allocations),
            "missions": {
                name: {
                    "auditors": list(stats["auditors"]),
                    "weeks": sorted(stats["weeks"]),
                    "total_days": round(stats["total_days"], 1)
                }
                for name, stats in mission_stats.items()
            },
            "auditors": {
                name: {
                    "missions": list(stats["missions"]),
                    "total_days": round(stats["total_days"], 1)
                }
                for name, stats in auditor_stats.items()
            },
            "external_resources_used": result.external_resources_needed,
            "external_days": sum(a.days for a in result.external_allocations)
        }


def create_default_optimizer(year: int = 2024) -> AuditStaffingOptimizer:
    """Crée un optimiseur avec configuration par défaut."""
    config = PlanningConfig(year=year)
    return AuditStaffingOptimizer(config)
