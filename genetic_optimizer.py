"""
Optimiseur génétique robuste pour le staffing des missions d'audit.
Utilise un algorithme génétique avec multiprocessing.
CONTRAINTE STRICTE: Un auditeur ne peut être que sur UNE SEULE mission par semaine.
"""

import random
import math
import os
from typing import List, Dict, Tuple, Set, Any, Optional
from dataclasses import dataclass, field
from multiprocessing import Pool, cpu_count
import multiprocessing
import copy

# Forcer le mode 'spawn' sur Windows pour éviter les problèmes avec threads
try:
    multiprocessing.set_start_method('spawn', force=False)
except RuntimeError:
    pass  # Déjà défini

from models import (
    Auditor, Mission, Allocation, OptimizationResult,
    PlanningConfig
)
from logger import logger


@dataclass
class Individual:
    """Un individu = une solution potentielle."""
    assignments: Dict[str, List[Tuple[str, Set[int]]]] = field(default_factory=dict)
    fitness: float = 0.0
    feasible: bool = False
    violations: int = 0
    conflicts: int = 0  # Nombre de conflits (auditeur sur 2 missions même semaine)


# ============ FONCTIONS UTILITAIRES ============

def _count_conflicts(assignments: Dict[str, List[Tuple[str, Any]]]) -> int:
    """Compte le nombre de conflits (auditeur sur 2+ missions même semaine)."""
    auditor_weeks = {}
    conflicts = 0

    for m_name, m_assigns in assignments.items():
        for a_name, weeks in m_assigns:
            week_set = set(weeks) if not isinstance(weeks, set) else weeks
            if a_name not in auditor_weeks:
                auditor_weeks[a_name] = {}
            for w in week_set:
                if w in auditor_weeks[a_name]:
                    conflicts += 1
                else:
                    auditor_weeks[a_name][w] = m_name

    return conflicts


def _remove_conflicts(assignments: Dict[str, List[Tuple[str, Any]]],
                      missions_priority: List[str] = None) -> Dict[str, List[Tuple[str, Set[int]]]]:
    """Supprime les conflits en gardant la première assignation par mission prioritaire."""
    # Construire usage global des auditeurs (semaine -> mission)
    auditor_week_mission = {}  # {auditor: {week: mission_name}}

    # Ordre de priorité des missions (si fourni)
    if missions_priority:
        mission_order = {m: i for i, m in enumerate(missions_priority)}
    else:
        mission_order = {m: i for i, m in enumerate(assignments.keys())}

    # Trier les missions par priorité
    sorted_missions = sorted(assignments.keys(), key=lambda m: mission_order.get(m, 999))

    new_assignments = {}

    for m_name in sorted_missions:
        m_assigns = assignments.get(m_name, [])
        new_mission_assigns = []

        for a_name, weeks in m_assigns:
            week_set = set(weeks) if not isinstance(weeks, set) else weeks

            if a_name not in auditor_week_mission:
                auditor_week_mission[a_name] = {}

            # Ne garder que les semaines non conflictuelles
            valid_weeks = set()
            for w in week_set:
                if w not in auditor_week_mission[a_name]:
                    valid_weeks.add(w)
                    auditor_week_mission[a_name][w] = m_name

            if valid_weeks:
                new_mission_assigns.append((a_name, valid_weeks))

        new_assignments[m_name] = new_mission_assigns

    return new_assignments


# ============ FONCTIONS GLOBALES POUR MULTIPROCESSING ============

def _evaluate_individual_worker(args):
    """Worker pour évaluer un individu - CONFLITS = REJET TOTAL."""
    individual_data, missions_data, auditors_data, mission_weeks, internal_names, generation, max_subcontracting_days = args

    assignments = individual_data['assignments']

    # ====== VÉRIFICATION STRICTE DES CONFLITS ======
    conflicts = _count_conflicts(assignments)

    if conflicts > 0:
        # Solution INVALIDE - fitness très négative
        return {
            'fitness': -10000 - conflicts * 1000,
            'feasible': False,
            'violations': conflicts,
            'conflicts': conflicts
        }

    # ====== VÉRIFICATION LIMITE SOUS-TRAITANCE (CONTRAINTE DURE) ======
    if max_subcontracting_days is not None:
        total_external_days = 0
        for m_name, m_assignments in assignments.items():
            for auditor_name, weeks in m_assignments:
                if auditor_name not in internal_names:  # Externe
                    total_external_days += len(weeks) * 5

        if total_external_days > max_subcontracting_days:
            # Solution INVALIDE - dépasse la limite de sous-traitance
            excess = total_external_days - max_subcontracting_days
            return {
                'fitness': -5000 - excess * 100,
                'feasible': False,
                'violations': int(excess / 5) + 1,
                'conflicts': 0
            }

    # Pas de conflits et sous-traitance OK - évaluer normalement
    fitness = 100.0
    violations = 0

    # ====== Évaluer chaque mission ======
    for m_data in missions_data:
        m_name = m_data['name']
        m_budget = m_data['budget_days']
        m_num_auditors = m_data['num_auditors']
        m_weeks_list = mission_weeks.get(m_name, [])

        mission_assignments = assignments.get(m_name, [])

        # Mission non assignée
        if not mission_assignments:
            fitness -= 50
            violations += 1
            continue

        # Calculer les jours alloués
        total_days = sum(len(weeks) * 5 for _, weeks in mission_assignments)

        # Écart budget - PÉNALITÉ TRÈS FORTE pour respecter le budget
        if m_budget > 0:
            budget_diff = abs(total_days - m_budget)
            budget_ratio = total_days / m_budget

            if budget_ratio < 0.5:
                # Moins de 50% du budget = pénalité catastrophique
                fitness -= 500 * (1 - budget_ratio)  # Ex: 20% = -400 points
                violations += 5
            elif budget_ratio < 0.8:
                # Moins de 80% du budget = pénalité très forte
                fitness -= 200 * (1 - budget_ratio)  # Ex: 50% = -100 points
                violations += 2
            elif budget_ratio < 0.95:
                # 80-95% du budget = pénalité modérée
                fitness -= 50 * (1 - budget_ratio)
                violations += 1
            elif budget_ratio > 1.2:
                # Plus de 120% du budget = pénalité modérée
                fitness -= 50 * (budget_ratio - 1)
                violations += 1
            elif 0.95 <= budget_ratio <= 1.05:
                # Dans les 5% = bonus
                fitness += 30

        # Nombre d'auditeurs total
        num_assigned = len(mission_assignments)
        if num_assigned < m_num_auditors:
            fitness -= 15 * (m_num_auditors - num_assigned)
            violations += 1
        elif num_assigned >= m_num_auditors:
            fitness += 3

        # Nombre d'auditeurs CONSTANT par semaine (équipe fixe)
        week_auditor_count = {}
        for auditor_name, weeks in mission_assignments:
            for w in weeks:
                week_auditor_count[w] = week_auditor_count.get(w, 0) + 1

        # Pénaliser si le nombre d'auditeurs varie d'une semaine à l'autre
        for w, count in week_auditor_count.items():
            if count != m_num_auditors:
                fitness -= 20 * abs(count - m_num_auditors)
                violations += 1

        # BLOC COMPACT: Continuité stricte - pas de trous
        all_weeks = set()
        for _, weeks in mission_assignments:
            all_weeks.update(weeks)

        if all_weeks and len(all_weeks) > 1:
            sorted_weeks = sorted(all_weeks)
            gaps = 0
            for i in range(1, len(sorted_weeks)):
                for w in range(sorted_weeks[i-1] + 1, sorted_weeks[i]):
                    if w in m_weeks_list:
                        gaps += 1
            if gaps > 0:
                fitness -= 50 * gaps  # Pénalité forte pour les trous
                violations += gaps

        # ÉQUIPE FIXE: tous les auditeurs doivent travailler toutes les semaines actives
        if all_weeks and mission_assignments:
            for auditor_name, auditor_weeks in mission_assignments:
                missing_weeks = all_weeks - set(auditor_weeks)
                if missing_weeks:
                    # Auditeur ne travaille pas toutes les semaines = violation
                    fitness -= 30 * len(missing_weeks)
                    violations += len(missing_weeks)

        # Bonus internes, PÉNALITÉ FORTE pour sous-traitants (minimiser leur usage)
        for auditor_name, weeks in mission_assignments:
            if auditor_name in internal_names:
                fitness += len(weeks) * 1.0  # Bonus pour internes
            else:
                fitness -= len(weeks) * 5.0  # Pénalité forte pour sous-traitants

    # Vérifier la capacité des auditeurs
    auditor_usage = {}
    for m_data in missions_data:
        m_name = m_data['name']
        for auditor_name, weeks in assignments.get(m_name, []):
            if auditor_name not in auditor_usage:
                auditor_usage[auditor_name] = set()
            auditor_usage[auditor_name].update(weeks)

    auditor_budgets = {a['name']: a['budget_days'] for a in auditors_data}
    for auditor_name, weeks_used in auditor_usage.items():
        total_days = len(weeks_used) * 5
        budget = auditor_budgets.get(auditor_name, 260)
        if total_days > budget:
            over = (total_days - budget) / budget
            fitness -= 10 * over
            violations += 1

    # ====== CHARGE MINIMALE 80% POUR AUDITEURS INTERNES ======
    # Seulement si le volume de travail le permet
    MIN_LOAD_RATIO = 0.80
    total_mission_budget = sum(m['budget_days'] for m in missions_data)
    internal_auditors = [a for a in auditors_data if not a.get('is_external', False)]
    total_internal_capacity = sum(a['budget_days'] for a in internal_auditors)

    # Appliquer la pénalité seulement s'il y a assez de travail
    if total_mission_budget >= total_internal_capacity * MIN_LOAD_RATIO:
        for a in internal_auditors:
            auditor_name = a['name']
            budget = a['budget_days']
            min_days = MIN_LOAD_RATIO * budget

            weeks_used = auditor_usage.get(auditor_name, set())
            total_days = len(weeks_used) * 5

            if total_days < min_days:
                # Pénalité proportionnelle au manque de charge
                load_ratio = total_days / budget if budget > 0 else 0
                deficit_ratio = MIN_LOAD_RATIO - load_ratio
                # Pénalité forte: 100 points par 10% sous les 80%
                fitness -= 100 * (deficit_ratio / 0.10)
                violations += 1

    feasible = (violations == 0)

    return {
        'fitness': fitness,
        'feasible': feasible,
        'violations': violations,
        'conflicts': 0
    }


def _create_individual_greedy(args):
    """Crée un individu avec stratégie gloutonne - SANS CONFLITS."""
    missions_data, auditors_data, mission_weeks, auditor_available_weeks, strategy = args

    assignments = {}
    # CRUCIAL: tracker l'usage des auditeurs pour éviter les conflits
    auditor_usage = {a['name']: set() for a in auditors_data}

    # Trier les missions
    if strategy == 'difficult_first':
        sorted_missions = sorted(
            missions_data,
            key=lambda m: len(mission_weeks.get(m['name'], []))
        )
    else:
        sorted_missions = list(missions_data)
        if strategy == 'random':
            random.shuffle(sorted_missions)

    for m_data in sorted_missions:
        m_name = m_data['name']
        m_budget = m_data['budget_days']
        m_num_auditors = max(1, m_data['num_auditors'])
        m_weeks = mission_weeks.get(m_name, [])

        if not m_weeks:
            assignments[m_name] = []
            continue

        # Calculer le nombre de semaines idéal (arrondir vers le HAUT pour couvrir le budget)
        weeks_needed = max(1, math.ceil(m_budget / (m_num_auditors * 5)))
        weeks_needed = min(weeks_needed, len(m_weeks))

        # Choisir les semaines
        if strategy == 'random':
            if len(m_weeks) > weeks_needed:
                start_idx = random.randint(0, len(m_weeks) - weeks_needed)
                selected_weeks = set(m_weeks[start_idx:start_idx + weeks_needed])
            else:
                selected_weeks = set(m_weeks)
        elif strategy == 'early':
            selected_weeks = set(m_weeks[:weeks_needed])
        elif strategy == 'late':
            selected_weeks = set(m_weeks[-weeks_needed:])
        else:  # middle ou difficult_first
            mid = len(m_weeks) // 2
            start = max(0, mid - weeks_needed // 2)
            selected_weeks = set(m_weeks[start:start + weeks_needed])

        # Trouver les auditeurs disponibles SANS CONFLIT
        # ===== SÉLECTION PAR COMPÉTENCES =====
        m_required_skills = m_data.get('required_skills', set())
        if isinstance(m_required_skills, list):
            m_required_skills = set(m_required_skills)

        # Construire la liste des candidats avec leurs compétences
        all_candidates = []
        for a_data in auditors_data:
            a_name = a_data['name']
            a_weeks = auditor_available_weeks.get(a_name, set())
            used_weeks = auditor_usage[a_name]
            a_skills = a_data.get('skills', set())

            # Semaines libres = disponibles ET non déjà utilisées
            free_weeks = (selected_weeks & a_weeks) - used_weeks

            if free_weeks:
                overlap_ratio = len(free_weeks) / len(selected_weeks) if selected_weeks else 0
                score = overlap_ratio * 10
                if not a_data['is_external']:
                    score += 20
                score -= len(used_weeks) * 0.1
                all_candidates.append({
                    'name': a_name,
                    'free_weeks': free_weeks,
                    'score': score,
                    'is_external': a_data['is_external'],
                    'skills': a_skills
                })

        all_candidates.sort(key=lambda x: x['score'], reverse=True)

        # Sélectionner les auditeurs en respectant les compétences
        mission_assignments = []
        used_auditors = set()

        # Si des compétences sont requises, sélectionner par compétence
        # IMPORTANT: Généraliste est une compétence spécifique, PAS un joker
        if m_required_skills and len(m_required_skills) > 0:
            num_skills = len(m_required_skills)
            auditors_per_skill = m_num_auditors // num_skills
            extra_auditors = m_num_auditors % num_skills

            for skill_idx, skill in enumerate(sorted(m_required_skills)):
                # Combien d'auditeurs pour cette compétence
                required_for_skill = auditors_per_skill + (1 if skill_idx < extra_auditors else 0)

                # Trouver les candidats avec cette compétence EXACTE uniquement
                # Généraliste n'est PAS un joker - c'est une compétence comme les autres
                skill_candidates = [
                    c for c in all_candidates
                    if skill in c['skills'] and c['name'] not in used_auditors
                ]

                # Sélectionner les meilleurs avec la compétence exacte
                for candidate in skill_candidates[:required_for_skill]:
                    if candidate['free_weeks']:
                        mission_assignments.append((candidate['name'], set(candidate['free_weeks'])))
                        auditor_usage[candidate['name']].update(candidate['free_weeks'])
                        used_auditors.add(candidate['name'])
        else:
            # Pas de compétences requises, sélection classique
            for candidate in all_candidates:
                if len(mission_assignments) >= m_num_auditors:
                    break
                if candidate['name'] not in used_auditors and candidate['free_weeks']:
                    mission_assignments.append((candidate['name'], set(candidate['free_weeks'])))
                    auditor_usage[candidate['name']].update(candidate['free_weeks'])
                    used_auditors.add(candidate['name'])

        # Compléter si nécessaire
        if len(mission_assignments) < m_num_auditors:
            for a_data in auditors_data:
                if len(mission_assignments) >= m_num_auditors:
                    break
                a_name = a_data['name']
                if a_name in [ma[0] for ma in mission_assignments]:
                    continue

                a_weeks = auditor_available_weeks.get(a_name, set())
                # Semaines libres sur TOUTE la mission
                free_weeks = (set(m_weeks) & a_weeks) - auditor_usage[a_name]

                if free_weeks:
                    # Prendre un bloc continu
                    sorted_free = sorted(free_weeks)
                    block = {sorted_free[0]}
                    for i in range(1, len(sorted_free)):
                        if sorted_free[i] == sorted_free[i-1] + 1:
                            block.add(sorted_free[i])
                        elif len(block) >= 2:
                            break
                        else:
                            block = {sorted_free[i]}

                    if block:
                        mission_assignments.append((a_name, block))
                        auditor_usage[a_name].update(block)

        assignments[m_name] = mission_assignments

    return {'assignments': assignments, 'fitness': 0.0, 'feasible': False, 'violations': 0, 'conflicts': 0}


def _create_individual_random(args):
    """Crée un individu aléatoire - SANS CONFLITS."""
    missions_data, auditors_data, mission_weeks, auditor_available_weeks, _ = args

    assignments = {}
    # CRUCIAL: tracker l'usage des auditeurs pour éviter les conflits
    auditor_usage = {a['name']: set() for a in auditors_data}

    # Ordre aléatoire des missions
    shuffled_missions = list(missions_data)
    random.shuffle(shuffled_missions)

    for m_data in shuffled_missions:
        m_name = m_data['name']
        m_budget = m_data['budget_days']
        m_num_auditors = max(1, m_data['num_auditors'])
        m_weeks = mission_weeks.get(m_name, [])

        if not m_weeks:
            assignments[m_name] = []
            continue

        # Nombre de semaines (basé sur le budget, arrondi vers le haut)
        ideal_weeks = math.ceil(m_budget / (m_num_auditors * 5))
        min_weeks = max(1, ideal_weeks - 1)
        max_weeks_calc = min(len(m_weeks), ideal_weeks + 1)
        weeks_needed = random.randint(min_weeks, max(min_weeks, max_weeks_calc))

        # Sélectionner un bloc de semaines
        if len(m_weeks) > weeks_needed:
            start_idx = random.randint(0, len(m_weeks) - weeks_needed)
            selected_weeks = set(m_weeks[start_idx:start_idx + weeks_needed])
        else:
            selected_weeks = set(m_weeks)

        # Ordre aléatoire des auditeurs (internes d'abord)
        shuffled_auditors = list(auditors_data)
        random.shuffle(shuffled_auditors)
        internals = [a for a in shuffled_auditors if not a['is_external']]
        externals = [a for a in shuffled_auditors if a['is_external']]
        ordered_auditors = internals + externals

        mission_assignments = []
        for a_data in ordered_auditors:
            if len(mission_assignments) >= m_num_auditors:
                break

            a_name = a_data['name']
            if a_name in [ma[0] for ma in mission_assignments]:
                continue

            a_weeks = auditor_available_weeks.get(a_name, set())
            # Semaines libres = disponibles ET non déjà utilisées
            free_weeks = (selected_weeks & a_weeks) - auditor_usage[a_name]

            if free_weeks:
                mission_assignments.append((a_name, set(free_weeks)))
                auditor_usage[a_name].update(free_weeks)

        # Compléter si nécessaire
        if len(mission_assignments) < m_num_auditors:
            for a_data in ordered_auditors:
                if len(mission_assignments) >= m_num_auditors:
                    break
                a_name = a_data['name']
                if a_name in [ma[0] for ma in mission_assignments]:
                    continue

                a_weeks = auditor_available_weeks.get(a_name, set())
                free_weeks = (set(m_weeks) & a_weeks) - auditor_usage[a_name]

                if free_weeks:
                    num_to_take = min(len(free_weeks), weeks_needed)
                    taken = set(random.sample(sorted(free_weeks), num_to_take))
                    mission_assignments.append((a_name, taken))
                    auditor_usage[a_name].update(taken)

        assignments[m_name] = mission_assignments

    return {'assignments': assignments, 'fitness': 0.0, 'feasible': False, 'violations': 0, 'conflicts': 0}


def _repair_conflicts_worker(args):
    """Répare un individu en supprimant TOUS les conflits."""
    individual_data, missions_data, auditors_data, mission_weeks, auditor_available_weeks = args

    assignments = individual_data['assignments']

    # Supprimer tous les conflits
    clean_assignments = _remove_conflicts(assignments)

    # Reconstruire l'usage des auditeurs
    auditor_usage = {a['name']: set() for a in auditors_data}
    for m_name, m_assigns in clean_assignments.items():
        for a_name, weeks in m_assigns:
            auditor_usage[a_name].update(weeks)

    # Compléter les missions sous-staffées
    for m_data in missions_data:
        m_name = m_data['name']
        m_num_auditors = max(1, m_data['num_auditors'])
        m_weeks_list = mission_weeks.get(m_name, [])

        if not m_weeks_list:
            continue

        current = list(clean_assignments.get(m_name, []))

        while len(current) < m_num_auditors:
            # Trouver les semaines de la mission
            mission_weeks_used = set()
            for _, weeks in current:
                mission_weeks_used.update(weeks)

            if not mission_weeks_used:
                mission_weeks_used = set(m_weeks_list[:min(4, len(m_weeks_list))])

            # Chercher un auditeur disponible
            current_auditors = {c[0] for c in current}
            best_auditor = None
            best_weeks = set()

            for a_data in auditors_data:
                a_name = a_data['name']
                if a_name in current_auditors:
                    continue

                a_available = auditor_available_weeks.get(a_name, set())
                used = auditor_usage.get(a_name, set())
                free = (mission_weeks_used & a_available) - used

                if len(free) > len(best_weeks):
                    best_auditor = a_name
                    best_weeks = free

            if best_auditor and best_weeks:
                current.append((best_auditor, best_weeks))
                auditor_usage[best_auditor].update(best_weeks)
            else:
                break

        clean_assignments[m_name] = current

    return {'assignments': clean_assignments, 'fitness': 0.0, 'feasible': False, 'violations': 0, 'conflicts': 0}


class GeneticAuditOptimizer:
    """
    Optimiseur génétique pour le staffing d'audit.
    GARANTIE: Aucun conflit (auditeur sur 2 missions même semaine).
    """

    def __init__(
        self,
        config: PlanningConfig,
        population_size: int = 300,
        generations: int = 500,
        mutation_rate: float = 0.20,
        crossover_rate: float = 0.80,
        elite_size: int = 30,
        max_subcontracting_days: float = None
    ):
        self.config = config
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_size = elite_size
        self.max_subcontracting_days = max_subcontracting_days
        self.num_workers = max(1, cpu_count() or 4)
        self.current_generation = 0
        # Info de progression pour affichage temps réel
        self.progress_info = {
            'generation': 0,
            'total_generations': self.generations,
            'best_fitness': 0,
            'best_conflicts': 0,
            'feasible_count': 0,
            'population_size': self.population_size,
            'stagnation': 0,
            'status': 'Initialisation...'
        }

    def optimize(
        self,
        missions: List[Mission],
        auditors: List[Auditor],
        verbose: bool = False
    ) -> OptimizationResult:
        """Lance l'optimisation génétique."""
        result = OptimizationResult(success=False, messages=[])

        if not missions or not auditors:
            result.messages.append("Pas de missions ou d'auditeurs.")
            return result

        self.missions = missions
        self.auditors = list(auditors)

        # Convertir en structures sérialisables
        self.missions_data = [
            {
                'name': m.name,
                'budget_days': m.budget_days,
                'num_auditors': m.num_auditors,
                'supervisor_name': m.supervisor_name,
                'required_skills': {s.value for s in m.required_skills} if m.required_skills else set()
            }
            for m in missions
        ]

        self.auditors_data = [
            {
                'name': a.name,
                'budget_days': a.budget_days,
                'is_external': a.is_external,
                'skills': {s.value for s in a.skills} if a.skills else {'Généraliste'}
            }
            for a in self.auditors
        ]

        # TOUJOURS créer des sous-traitants de backup (sans aucune contrainte)
        # Ils ne seront utilisés que si nécessaire (pénalisés dans le fitness)
        internal_auditors = [a for a in self.auditors if not a.is_external]
        total_days_needed = sum(m.budget_days for m in missions)
        total_days_available = sum(a.budget_days for a in internal_auditors)
        deficit = max(0, total_days_needed - total_days_available)

        # Créer suffisamment de sous-traitants pour couvrir tous les cas
        max_auditors_needed = max(m.num_auditors for m in missions) if missions else 1
        num_external = max(20, int(deficit / 150) + max_auditors_needed * 2)

        if deficit > 0:
            logger.warning("Génétique", f"Déficit de {deficit:.0f}j. {num_external} sous-traitants disponibles.")
        else:
            logger.info("Génétique", f"Capacité OK. {num_external} sous-traitants disponibles si conflits.")

        from models import Skill
        all_skills = {'IT', 'Modèle', 'Assurance', 'Généraliste'}
        for i in range(num_external):
            ext_name = f"Sous-traitant_{i+1}"
            ext = Auditor(
                name=ext_name,
                budget_days=260,
                skills={Skill.IT, Skill.MODELE, Skill.ASSURANCE, Skill.GENERALISTE},
                is_external=True,
                unavailable_weeks=set()  # AUCUNE contrainte - disponible toutes les semaines
            )
            self.auditors.append(ext)
            self.auditors_data.append({
                'name': ext_name,
                'budget_days': 260,
                'is_external': True,
                'skills': all_skills  # Sous-traitants ont toutes les compétences
            })

        # Pré-calculer les semaines valides
        self.mission_weeks = {}
        for m in missions:
            self.mission_weeks[m.name] = sorted(m.allowed_weeks - self.config.blocked_weeks)

        self.auditor_available_weeks = {}
        for a in self.auditors:
            available = set(range(1, 53)) - a.unavailable_weeks - self.config.blocked_weeks
            self.auditor_available_weeks[a.name] = available

        self.internal_names = {a.name for a in self.auditors if not a.is_external}

        if verbose:
            print(f"[Génétique] {self.num_workers} CPU, {len(missions)} missions, {len(self.auditors)} auditeurs")

        # Initialiser population
        population = self._initialize_diverse_population()

        if verbose:
            print(f"[Génétique] Population initiale créée")

        # Évaluer
        self.progress_info['status'] = 'Évaluation population initiale...'
        self._evaluate_population_parallel(population)

        best_individual = max(population, key=lambda x: x.fitness)
        best_ever = copy.deepcopy(best_individual)
        stagnation_counter = 0
        adaptive_mutation = self.mutation_rate

        # Mise à jour progression initiale
        feasible_count = sum(1 for ind in population if ind.feasible)
        self.progress_info.update({
            'generation': 0,
            'best_fitness': best_ever.fitness,
            'best_conflicts': best_ever.conflicts,
            'feasible_count': feasible_count,
            'stagnation': 0,
            'status': 'Évolution en cours...'
        })

        if verbose:
            print(f"[Génétique] Gen 0: fitness={best_individual.fitness:.1f}, conflits={best_individual.conflicts}")

        # Évolution
        for gen in range(1, self.generations + 1):
            self.current_generation = gen

            parents = self._selection(population)
            offspring = self._create_offspring(parents, adaptive_mutation)

            # Réparation des conflits toutes les 5 générations
            if gen % 5 == 0:
                self._repair_conflicts(offspring)

            self._evaluate_population_parallel(offspring)

            # Élitisme
            population.sort(key=lambda x: x.fitness, reverse=True)
            elite = population[:self.elite_size]

            offspring.sort(key=lambda x: x.fitness, reverse=True)
            population = elite + offspring[:self.population_size - self.elite_size]

            current_best = max(population, key=lambda x: x.fitness)
            if current_best.fitness > best_ever.fitness:
                best_ever = copy.deepcopy(current_best)
                stagnation_counter = 0
                adaptive_mutation = self.mutation_rate
            else:
                stagnation_counter += 1
                adaptive_mutation = min(0.5, adaptive_mutation * 1.02)

            # Mise à jour progression
            feasible_count = sum(1 for ind in population if ind.feasible)
            zero_conflict_count = sum(1 for ind in population if ind.conflicts == 0)
            self.progress_info.update({
                'generation': gen,
                'best_fitness': best_ever.fitness,
                'best_conflicts': best_ever.conflicts,
                'feasible_count': feasible_count,
                'zero_conflict_count': zero_conflict_count,
                'stagnation': stagnation_counter,
                'status': 'Diversification...' if stagnation_counter >= 35 else 'Évolution en cours...'
            })

            if stagnation_counter >= 40:
                if verbose:
                    print(f"[Génétique] Gen {gen}: Diversification")
                num_replace = self.population_size // 2
                new_individuals = self._initialize_diverse_population(num_replace)
                self._evaluate_population_parallel(new_individuals)
                population = population[:self.population_size - num_replace] + new_individuals
                stagnation_counter = 0
                adaptive_mutation = self.mutation_rate

            if verbose and gen % 25 == 0:
                print(f"[Génétique] Gen {gen}: fitness={best_ever.fitness:.1f}, conflits={best_ever.conflicts}")

            # Arrêt si solution sans conflit et bonne fitness
            if best_ever.conflicts == 0 and best_ever.fitness > 50:
                if verbose:
                    print(f"[Génétique] Solution trouvée à la génération {gen}")
                break

        # VALIDATION FINALE: s'assurer qu'il n'y a AUCUN conflit
        if best_ever.conflicts > 0:
            if verbose:
                print(f"[Génétique] Réparation finale des conflits...")
            best_ever = self._final_repair(best_ever)

        # RÉPARATION BUDGET: essayer de compléter les missions sous-budget
        best_ever = self._repair_budgets(best_ever, verbose)

        # Convertir en résultat
        if best_ever.conflicts == 0 and best_ever.fitness > -100:
            allocations = self._individual_to_allocations(best_ever)

            result.success = True
            result.allocations = [a for a in allocations if not a.auditor_name.startswith("Sous-traitant_")]
            result.external_allocations = [a for a in allocations if a.auditor_name.startswith("Sous-traitant_")]
            result.external_resources_needed = len(result.external_allocations) > 0

            # VALIDATION FINALE: supprimer doublons et conflits
            duplicates = result.deduplicate_allocations()
            if duplicates:
                logger.warning("Génétique", f"{duplicates} doublons supprimés automatiquement")

            conflicts = result.check_conflicts()
            if conflicts:
                removed = result.remove_conflicts()
                logger.warning("Génétique", f"{removed} conflits corrigés automatiquement")

            status = "faisable" if best_ever.feasible else f"{best_ever.violations} ajustements mineurs"
            if result.external_allocations:
                ext_days = sum(a.days for a in result.external_allocations)
                # Vérifier si la limite de sous-traitance est respectée
                if self.max_subcontracting_days is not None:
                    if ext_days > self.max_subcontracting_days:
                        logger.error("Génétique", f"LIMITE SOUS-TRAITANCE DÉPASSÉE: {ext_days:.0f}j > {self.max_subcontracting_days:.0f}j max (contrainte dure non respectée)")
                    else:
                        logger.info("Génétique", f"Sous-traitance: {ext_days:.0f}j / {self.max_subcontracting_days:.0f}j max")
                logger.info("Génétique", f"Solution ({status}) avec {ext_days:.0f}j sous-traitance")
            else:
                logger.info("Génétique", f"Solution ({status})")
        else:
            result.messages.append(f"Aucune solution sans conflit (fitness={best_ever.fitness:.1f})")

        return result

    def _check_allocation_conflicts(self, allocations: List[Allocation]) -> int:
        """Vérifie qu'aucun auditeur n'est sur 2 missions la même semaine."""
        auditor_weeks = {}
        conflicts = 0

        for alloc in allocations:
            key = (alloc.auditor_name, alloc.week)
            if key in auditor_weeks:
                if auditor_weeks[key] != alloc.mission_name:
                    conflicts += 1
            else:
                auditor_weeks[key] = alloc.mission_name

        return conflicts

    def _final_repair(self, individual: Individual) -> Individual:
        """Réparation finale pour garantir aucun conflit."""
        clean_assignments = _remove_conflicts(individual.assignments)
        individual.assignments = {
            k: [(a, set(w)) for a, w in v]
            for k, v in clean_assignments.items()
        }
        individual.conflicts = 0
        return individual

    def _repair_budgets(self, individual: Individual, verbose: bool = False) -> Individual:
        """
        Répare les missions sous-budget en ajoutant des semaines avec des sous-traitants.
        RESPECTE la limite max_subcontracting_days si définie.
        """
        assignments = individual.assignments

        # Collecter les semaines déjà utilisées par chaque auditeur
        auditor_used_weeks = {}
        for m_name, m_assignments in assignments.items():
            for auditor_name, weeks in m_assignments:
                if auditor_name not in auditor_used_weeks:
                    auditor_used_weeks[auditor_name] = set()
                auditor_used_weeks[auditor_name].update(weeks)

        # Calculer les jours sous-traitance actuels
        current_external_days = 0
        for m_name, m_assignments in assignments.items():
            for auditor_name, weeks in m_assignments:
                if auditor_name not in self.internal_names:
                    current_external_days += len(weeks) * 5

        # Limite sous-traitance restante
        remaining_external_budget = None
        if self.max_subcontracting_days is not None:
            remaining_external_budget = self.max_subcontracting_days - current_external_days
            if remaining_external_budget <= 0:
                # Limite déjà atteinte, ne pas ajouter de sous-traitants
                if verbose:
                    logger.warning("Génétique", f"Limite sous-traitance atteinte ({current_external_days}j/{self.max_subcontracting_days}j), pas de réparation budget")
                return individual

        # Liste des sous-traitants disponibles
        subcontractors = [a for a in self.auditors_data if a['is_external']]

        # Pour chaque mission, vérifier le budget
        for m_data in self.missions_data:
            m_name = m_data['name']
            m_budget = m_data['budget_days']
            m_num_auditors = max(1, m_data['num_auditors'])
            m_weeks_available = self.mission_weeks.get(m_name, [])

            if not m_weeks_available:
                continue

            # Calculer les jours actuellement alloués
            current_assignments = assignments.get(m_name, [])
            current_days = sum(len(weeks) * 5 for _, weeks in current_assignments)

            # Si sous-budget, essayer de compléter
            if current_days < m_budget * 0.95:
                days_needed = m_budget - current_days
                weeks_needed = max(1, math.ceil(days_needed / (m_num_auditors * 5)))

                # Trouver les semaines de la mission qui ne sont pas encore utilisées
                used_weeks_for_mission = set()
                for _, weeks in current_assignments:
                    used_weeks_for_mission.update(weeks)

                available_weeks = [w for w in m_weeks_available if w not in used_weeks_for_mission]

                if not available_weeks:
                    continue

                # Trouver un bloc contigu de semaines disponibles
                # (Priorité aux semaines adjacentes aux semaines existantes)
                if used_weeks_for_mission:
                    # Étendre avant ou après le bloc existant
                    min_week = min(used_weeks_for_mission)
                    max_week = max(used_weeks_for_mission)

                    # Semaines avant le bloc
                    before_weeks = [w for w in available_weeks if w < min_week]
                    # Semaines après le bloc
                    after_weeks = [w for w in available_weeks if w > max_week]

                    # Combiner, priorité aux semaines adjacentes
                    selected_weeks = []
                    for w in sorted(before_weeks, reverse=True)[:weeks_needed]:
                        selected_weeks.append(w)
                    for w in sorted(after_weeks)[:weeks_needed - len(selected_weeks)]:
                        selected_weeks.append(w)
                else:
                    # Pas de semaines existantes, prendre les premières disponibles
                    selected_weeks = available_weeks[:weeks_needed]

                if not selected_weeks:
                    continue

                # Ajouter des sous-traitants pour ces semaines
                new_assignments = list(current_assignments)

                for week in selected_weeks:
                    # Vérifier limite sous-traitance AVANT d'ajouter
                    if remaining_external_budget is not None and remaining_external_budget <= 0:
                        break

                    # Trouver m_num_auditors sous-traitants disponibles pour cette semaine
                    auditors_this_week = sum(1 for _, ws in new_assignments if week in ws)
                    needed_this_week = m_num_auditors - auditors_this_week

                    for sub in subcontractors:
                        if needed_this_week <= 0:
                            break

                        # Vérifier limite sous-traitance
                        if remaining_external_budget is not None and remaining_external_budget < 5:
                            break

                        sub_name = sub['name']
                        sub_used = auditor_used_weeks.get(sub_name, set())

                        if week not in sub_used:
                            # Ajouter ce sous-traitant pour cette semaine
                            # Chercher s'il est déjà dans les assignments
                            found = False
                            for i, (a_name, a_weeks) in enumerate(new_assignments):
                                if a_name == sub_name:
                                    a_weeks.add(week)
                                    found = True
                                    break

                            if not found:
                                new_assignments.append((sub_name, {week}))

                            # Marquer comme utilisé
                            if sub_name not in auditor_used_weeks:
                                auditor_used_weeks[sub_name] = set()
                            auditor_used_weeks[sub_name].add(week)
                            needed_this_week -= 1

                            # Décrémenter le budget sous-traitance restant
                            if remaining_external_budget is not None:
                                remaining_external_budget -= 5

                assignments[m_name] = new_assignments

                if verbose:
                    new_days = sum(len(weeks) * 5 for _, weeks in new_assignments)
                    logger.info("Génétique", f"Réparation budget {m_name}: {current_days}j -> {new_days}j (budget: {m_budget}j)")

        individual.assignments = assignments
        return individual

    def _initialize_diverse_population(self, size: int = None) -> List[Individual]:
        """Crée une population diversifiée."""
        if size is None:
            size = self.population_size

        strategies = ['random', 'early', 'late', 'middle', 'difficult_first']

        args_list = []
        for i in range(size):
            strategy = strategies[i % len(strategies)]
            args_list.append((
                self.missions_data,
                self.auditors_data,
                self.mission_weeks,
                {k: v for k, v in self.auditor_available_weeks.items()},
                strategy
            ))

        population = []

        # Essayer multiprocessing, fallback séquentiel si échec
        try:
            with Pool(processes=self.num_workers) as pool:
                greedy_results = pool.map(_create_individual_greedy, args_list[:size // 2])
                random_results = pool.map(_create_individual_random, args_list[size // 2:])
        except Exception:
            # Fallback séquentiel (ex: appelé depuis un thread sur Windows)
            greedy_results = [_create_individual_greedy(a) for a in args_list[:size // 2]]
            random_results = [_create_individual_random(a) for a in args_list[size // 2:]]

        for r in greedy_results + random_results:
            ind = Individual(
                assignments={k: [(a, set(w) if isinstance(w, (list, set)) else w) for a, w in v]
                            for k, v in r['assignments'].items()},
                fitness=r['fitness'],
                feasible=r['feasible'],
                violations=r.get('violations', 0),
                conflicts=r.get('conflicts', 0)
            )
            population.append(ind)

        return population

    def _evaluate_population_parallel(self, population: List[Individual]) -> None:
        """Évalue tous les individus."""
        args_list = []
        for ind in population:
            ind_data = {
                'assignments': {
                    k: [(a, list(w)) for a, w in v]
                    for k, v in ind.assignments.items()
                }
            }
            args_list.append((
                ind_data,
                self.missions_data,
                self.auditors_data,
                self.mission_weeks,
                self.internal_names,
                self.current_generation,
                self.max_subcontracting_days  # Limite sous-traitance (contrainte dure)
            ))

        # Essayer multiprocessing, fallback séquentiel si échec
        try:
            with Pool(processes=self.num_workers) as pool:
                results = pool.map(_evaluate_individual_worker, args_list)
        except Exception:
            results = [_evaluate_individual_worker(a) for a in args_list]

        for ind, r in zip(population, results):
            ind.fitness = r['fitness']
            ind.feasible = r['feasible']
            ind.violations = r['violations']
            ind.conflicts = r['conflicts']

    def _repair_conflicts(self, population: List[Individual]) -> None:
        """Répare les individus avec conflits."""
        to_repair = [ind for ind in population if ind.conflicts > 0 or _count_conflicts(ind.assignments) > 0]

        if not to_repair:
            return

        args_list = []
        for ind in to_repair:
            ind_data = {
                'assignments': {
                    k: [(a, list(w)) for a, w in v]
                    for k, v in ind.assignments.items()
                }
            }
            args_list.append((
                ind_data,
                self.missions_data,
                self.auditors_data,
                self.mission_weeks,
                {k: v for k, v in self.auditor_available_weeks.items()}
            ))

        # Essayer multiprocessing, fallback séquentiel si échec
        try:
            with Pool(processes=self.num_workers) as pool:
                results = pool.map(_repair_conflicts_worker, args_list)
        except Exception:
            results = [_repair_conflicts_worker(a) for a in args_list]

        for ind, r in zip(to_repair, results):
            ind.assignments = {
                k: [(a, set(w)) for a, w in v]
                for k, v in r['assignments'].items()
            }
            ind.conflicts = 0

    def _selection(self, population: List[Individual]) -> List[Individual]:
        """Sélection par tournoi - priorité aux solutions sans conflit."""
        tournament_size = 5
        selected = []

        for _ in range(self.population_size):
            tournament = random.sample(population, min(tournament_size, len(population)))
            # Priorité absolue: pas de conflits
            no_conflict = [t for t in tournament if t.conflicts == 0]
            if no_conflict:
                winner = max(no_conflict, key=lambda x: x.fitness)
            else:
                winner = min(tournament, key=lambda x: x.conflicts)
            selected.append(winner)

        return selected

    def _create_offspring(self, parents: List[Individual], mutation_rate: float) -> List[Individual]:
        """Crée les enfants."""
        offspring = []

        for i in range(0, len(parents) - 1, 2):
            child1, child2 = self._crossover(parents[i], parents[i + 1])
            child1 = self._mutate(child1, mutation_rate)
            child2 = self._mutate(child2, mutation_rate)
            # Réparer les conflits immédiatement après mutation
            self._repair_individual_conflicts(child1)
            self._repair_individual_conflicts(child2)
            offspring.extend([child1, child2])

        while len(offspring) < self.population_size - self.elite_size:
            strategy = random.choice(['random', 'early', 'late', 'middle'])
            args = (self.missions_data, self.auditors_data, self.mission_weeks,
                    {k: v for k, v in self.auditor_available_weeks.items()}, strategy)
            r = _create_individual_greedy(args)
            ind = Individual(
                assignments={k: [(a, set(w)) for a, w in v] for k, v in r['assignments'].items()}
            )
            offspring.append(ind)

        return offspring

    def _repair_individual_conflicts(self, individual: Individual) -> None:
        """Répare les conflits d'un individu localement."""
        conflicts = _count_conflicts(individual.assignments)
        if conflicts > 0:
            clean = _remove_conflicts(individual.assignments)
            individual.assignments = {
                k: [(a, set(w)) for a, w in v]
                for k, v in clean.items()
            }

    def _crossover(self, parent1: Individual, parent2: Individual) -> Tuple[Individual, Individual]:
        """Croisement."""
        if random.random() > self.crossover_rate:
            return copy.deepcopy(parent1), copy.deepcopy(parent2)

        child1_assignments = {}
        child2_assignments = {}

        for m in self.missions:
            p1_assign = parent1.assignments.get(m.name, [])
            p2_assign = parent2.assignments.get(m.name, [])

            if random.random() < 0.5:
                child1_assignments[m.name] = [(a, set(w)) for a, w in p1_assign]
                child2_assignments[m.name] = [(a, set(w)) for a, w in p2_assign]
            else:
                child1_assignments[m.name] = [(a, set(w)) for a, w in p2_assign]
                child2_assignments[m.name] = [(a, set(w)) for a, w in p1_assign]

        return Individual(assignments=child1_assignments), Individual(assignments=child2_assignments)

    def _mutate(self, individual: Individual, mutation_rate: float) -> Individual:
        """Mutation."""
        if random.random() > mutation_rate:
            return individual

        mission = random.choice(self.missions)
        mission_weeks_list = self.mission_weeks.get(mission.name, [])

        if not mission_weeks_list:
            return individual

        mutation_type = random.choice([
            'shift_weeks', 'expand_weeks', 'change_auditor',
            'add_auditor', 'remove_auditor', 'replace_all'
        ])

        assignments = list(individual.assignments.get(mission.name, []))

        # Calculer les semaines déjà utilisées par cet individu (toutes missions)
        auditor_usage = {}
        for m_name, m_assigns in individual.assignments.items():
            if m_name == mission.name:
                continue  # On va modifier cette mission
            for a_name, weeks in m_assigns:
                if a_name not in auditor_usage:
                    auditor_usage[a_name] = set()
                auditor_usage[a_name].update(weeks)

        if mutation_type == 'shift_weeks' and assignments:
            idx = random.randint(0, len(assignments) - 1)
            auditor_name, old_weeks = assignments[idx]
            if old_weeks:
                shift = random.randint(-4, 4)
                new_weeks = set()
                used = auditor_usage.get(auditor_name, set())
                for w in old_weeks:
                    new_w = w + shift
                    if (new_w in mission_weeks_list and
                        new_w in self.auditor_available_weeks.get(auditor_name, set()) and
                        new_w not in used):
                        new_weeks.add(new_w)
                if new_weeks:
                    assignments[idx] = (auditor_name, new_weeks)

        elif mutation_type == 'expand_weeks' and assignments:
            idx = random.randint(0, len(assignments) - 1)
            auditor_name, weeks = assignments[idx]
            if weeks:
                available = self.auditor_available_weeks.get(auditor_name, set())
                used = auditor_usage.get(auditor_name, set())
                new_weeks = set(weeks)
                for w in list(weeks):
                    for adj in [w - 1, w + 1]:
                        if adj in mission_weeks_list and adj in available and adj not in used:
                            new_weeks.add(adj)
                assignments[idx] = (auditor_name, new_weeks)

        elif mutation_type == 'change_auditor' and assignments:
            idx = random.randint(0, len(assignments) - 1)
            _, weeks = assignments[idx]
            new_auditor = random.choice(self.auditors)
            used = auditor_usage.get(new_auditor.name, set())
            available_weeks = (weeks & self.auditor_available_weeks.get(new_auditor.name, set())) - used
            if available_weeks:
                assignments[idx] = (new_auditor.name, available_weeks)

        elif mutation_type == 'add_auditor' and len(assignments) < len(self.auditors):
            current_auditors = {a[0] for a in assignments}
            available = [a for a in self.auditors if a.name not in current_auditors]
            if available and assignments:
                new_auditor = random.choice(available)
                all_weeks = set()
                for _, weeks in assignments:
                    all_weeks.update(weeks)
                used = auditor_usage.get(new_auditor.name, set())
                auditor_weeks = (all_weeks & self.auditor_available_weeks.get(new_auditor.name, set())) - used
                if auditor_weeks:
                    assignments.append((new_auditor.name, auditor_weeks))

        elif mutation_type == 'remove_auditor' and len(assignments) > 1:
            idx = random.randint(0, len(assignments) - 1)
            assignments.pop(idx)

        elif mutation_type == 'replace_all':
            m_budget = mission.budget_days
            m_num_auditors = max(1, mission.num_auditors)
            weeks_needed = max(1, math.ceil(m_budget / (m_num_auditors * 5)))
            weeks_needed = min(weeks_needed, len(mission_weeks_list))

            if len(mission_weeks_list) > weeks_needed:
                start_idx = random.randint(0, len(mission_weeks_list) - weeks_needed)
                selected_weeks = set(mission_weeks_list[start_idx:start_idx + weeks_needed])
            else:
                selected_weeks = set(mission_weeks_list)

            new_assignments = []
            shuffled = list(self.auditors)
            random.shuffle(shuffled)

            for a in shuffled:
                if len(new_assignments) >= m_num_auditors:
                    break
                a_weeks = self.auditor_available_weeks.get(a.name, set())
                used = auditor_usage.get(a.name, set())
                common = (selected_weeks & a_weeks) - used
                if common:
                    new_assignments.append((a.name, common))

            if new_assignments:
                assignments = new_assignments

        individual.assignments[mission.name] = assignments
        return individual

    def _individual_to_allocations(self, individual: Individual) -> List[Allocation]:
        """Convertit un individu en allocations."""
        allocations = []

        for m in self.missions:
            assignments = individual.assignments.get(m.name, [])
            for auditor_name, weeks in assignments:
                for w in weeks:
                    is_supervisor = (auditor_name == m.supervisor_name)
                    allocations.append(Allocation(
                        mission_name=m.name,
                        auditor_name=auditor_name,
                        week=w,
                        days=5.0,
                        is_supervisor=is_supervisor
                    ))

        return allocations


def create_genetic_optimizer(year: int = 2024) -> GeneticAuditOptimizer:
    """Crée un optimiseur génétique."""
    config = PlanningConfig(year=year)
    return GeneticAuditOptimizer(config)
