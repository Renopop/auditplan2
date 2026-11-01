"""
Optimiseur CP-SAT haute performance pour le staffing des missions d'audit.

Approche: modèle par blocs (pattern formulation) résolu avec Google OR-Tools CP-SAT.

Pourquoi c'est rapide ET précis comparé à l'ancien MILP PuLP/CBC :
  - Le temps de chaque mission est modélisé par un CHOIX DE BLOC contigu
    (1 booléen par placement possible) au lieu de variables jour-par-jour.
    -> Contiguïté et "pas de trous" garanties par construction (0 contrainte ajoutée).
  - La sous-traitance est un simple compteur entier par mission (ext[m]),
    pas des dizaines de faux auditeurs externes -> modèle beaucoup plus petit.
  - La disponibilité couple directement le placement du bloc et l'affectation
    (assign[m,a] + active[m,w] <= 1 si a indisponible en w) -> solutions valides.
  - CP-SAT est multi-thread et trouve l'optimum prouvé en quelques secondes.

Objectif (lexicographique via pondération) :
  1. Minimiser la sous-traitance (jours externes)        [poids très fort]
  2. Maximiser l'adéquation des compétences               [poids moyen]
  3. Équilibrer la charge entre auditeurs internes         [poids faible]
"""

from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict
import math
import os
import time

from models import (
    Auditor, Mission, Allocation, OptimizationResult,
    PlanningConfig, Skill
)
from constraints import ConstraintValidator

try:
    from ortools.sat.python import cp_model
    ORTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    ORTOOLS_AVAILABLE = False

try:
    from logger import logger
except Exception:  # pragma: no cover
    class _NullLogger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    logger = _NullLogger()


DAYS_PER_WEEK = 5.0


def _skill_score(auditor_skills: Set[Skill], required_skills: Set[Skill]) -> float:
    """
    Score d'adéquation d'un auditeur à une mission.
      - >0 : possède une/des compétence(s) spécialiste(s) requise(s)
      - 0.5 : pas de spécialité mais généraliste (peut aider)
      - 0 : ne convient pas
    """
    if not required_skills:
        return 1.0
    specialist = len(auditor_skills & required_skills)
    if specialist > 0:
        return float(specialist)
    if Skill.GENERALISTE in auditor_skills:
        return 0.5
    return 0.0


def _has_skill(auditor_skills: Set[Skill], skill: Skill) -> bool:
    """Un auditeur couvre une compétence requise s'il la possède explicitement."""
    return skill in auditor_skills


def _contiguous_runs(weeks_sorted: List[int]) -> List[List[int]]:
    """Découpe une liste triée de semaines en suites d'entiers consécutifs."""
    runs: List[List[int]] = []
    if not weeks_sorted:
        return runs
    current = [weeks_sorted[0]]
    for w in weeks_sorted[1:]:
        if w == current[-1] + 1:
            current.append(w)
        else:
            runs.append(current)
            current = [w]
    runs.append(current)
    return runs


class CPAuditOptimizer:
    """Optimiseur de staffing basé sur CP-SAT (OR-Tools)."""

    def __init__(self, config: PlanningConfig):
        self.config = config
        self.progress_info = {
            'status': 'Initialisation...',
            'solver': 'CP-SAT',
            'num_variables': 0,
            'num_constraints': 0,
            'objective_value': None,
            'phase': 'init',
        }

    # ------------------------------------------------------------------ #
    #  Préparation                                                       #
    # ------------------------------------------------------------------ #
    def _mission_blocks(self, mission: Mission) -> Tuple[int, List[List[int]]]:
        """
        Calcule la longueur (en semaines) et la liste des blocs contigus
        candidats pour une mission, à l'intérieur de sa fenêtre autorisée.

        Returns:
            (block_len, [ [w1, w2, ...], ... ])
        """
        allowed = sorted(mission.allowed_weeks - self.config.blocked_weeks)
        if not allowed:
            return 0, []

        block_len = max(1, math.ceil(mission.budget_days / (mission.num_auditors * DAYS_PER_WEEK)))

        # Limiter à la plus longue suite contiguë disponible
        runs = _contiguous_runs(allowed)
        max_run = max(len(r) for r in runs)
        if block_len > max_run:
            block_len = max_run

        blocks: List[List[int]] = []
        for run in runs:
            for i in range(len(run) - block_len + 1):
                blocks.append(run[i:i + block_len])

        return block_len, blocks

    # ------------------------------------------------------------------ #
    #  Optimisation                                                      #
    # ------------------------------------------------------------------ #
    def optimize(
        self,
        missions: List[Mission],
        auditors: List[Auditor],
        verbose: bool = False,
        time_limit: int = 30,
        gap_tolerance: float = 0.0,
        max_subcontracting_days: Optional[float] = None,
        min_load_ratio: float = 0.80,
    ) -> OptimizationResult:
        if not ORTOOLS_AVAILABLE:
            return OptimizationResult(
                success=False,
                messages=["OR-Tools (ortools) n'est pas installé. Exécutez: pip install ortools"],
            )

        start_time = time.time()

        # ---- Validation ----
        validator = ConstraintValidator(self.config)
        is_valid, errors, warnings = validator.validate_all(missions, auditors)
        result = OptimizationResult(success=False, messages=list(warnings))
        if errors:
            result.messages = errors + result.messages
            return result

        internal = [a for a in auditors if not a.is_external]
        aud_index = {a.name: a for a in internal}

        self.progress_info.update({'status': 'Construction du modèle...', 'phase': 'build'})

        model = cp_model.CpModel()

        # ---- Pré-calcul des blocs par mission ----
        block_len: Dict[str, int] = {}
        blocks: Dict[str, List[List[int]]] = {}
        for m in missions:
            L, blks = self._mission_blocks(m)
            block_len[m.name] = L
            blocks[m.name] = blks

        # ---- Variables ----
        # blk[m][i] : bloc i choisi pour la mission m
        blk: Dict[str, List["cp_model.IntVar"]] = {}
        # active[m][w] : la mission m est active en semaine w (expression bool)
        active: Dict[Tuple[str, int], "cp_model.IntVar"] = {}
        # assign[m][a] : auditeur interne a affecté à la mission m
        assign: Dict[Tuple[str, str], "cp_model.IntVar"] = {}
        # ext[m] : nombre d'auditeurs sous-traités sur la mission m
        ext: Dict[str, "cp_model.IntVar"] = {}

        eligible: Dict[str, List[str]] = {}  # mission -> auditeurs internes éligibles

        def _participates(mm) -> bool:
            return bool(getattr(mm, "supervisor_participates", False))

        for m in missions:
            mblocks = blocks[m.name]
            # Choix du bloc (exactement un si des blocs existent)
            blk[m.name] = [model.NewBoolVar(f"blk_{m.name}_{i}") for i in range(len(mblocks))]
            if mblocks:
                model.AddExactlyOne(blk[m.name])

            # active[m,w] = somme des blocs contenant w
            weeks_in_window = sorted(set(w for b in mblocks for w in b))
            for w in weeks_in_window:
                a_var = model.NewBoolVar(f"active_{m.name}_{w}")
                covering = [blk[m.name][i] for i, b in enumerate(mblocks) if w in b]
                # active = OR des blocs couvrants ; comme exactement un bloc choisi,
                # active vaut 1 ssi le bloc choisi contient w.
                model.Add(a_var == sum(covering))
                active[(m.name, w)] = a_var

            # Auditeurs éligibles = exécutants (score de compétence > 0).
            # Les superviseurs sont des VALIDATEURS : ils ne sont PAS staffés comme
            # exécutants ordinaires (sinon ils consommeraient des ressources rares).
            elig = [
                a.name for a in internal
                if not a.is_supervisor and _skill_score(a.skills, m.required_skills) > 0
            ]
            # Le superviseur n'est ajouté à l'équipe que s'il participe explicitement.
            lead = getattr(m, "mission_lead_name", None)
            if _participates(m) and m.supervisor_name in aud_index and m.supervisor_name not in elig:
                elig.append(m.supervisor_name)
            # Le chef de mission désigné doit faire partie de l'équipe -> éligible
            if lead and lead in aud_index and lead not in elig:
                elig.append(lead)
            eligible[m.name] = elig

            for a_name in elig:
                assign[(m.name, a_name)] = model.NewBoolVar(f"assign_{m.name}_{a_name}")

            ext[m.name] = model.NewIntVar(0, m.num_auditors, f"ext_{m.name}")

        # ---- Contraintes ----
        n_constraints = 0

        for m in missions:
            elig = eligible[m.name]

            # (B) Taille d'équipe : internes affectés + sous-traités = num_auditors
            model.Add(
                sum(assign[(m.name, a)] for a in elig) + ext[m.name] == m.num_auditors
            )
            n_constraints += 1

            # (C) Superviseur = validateur : AUCUNE contrainte de staffing.
            #     Les cas opt-in (participation du superviseur, chef désigné) sont
            #     traités comme des PRÉFÉRENCES FORTES dans l'objectif (cf. plus bas),
            #     jamais comme des contraintes dures -> le plan ne devient jamais
            #     infaisable même si la personne est indisponible/surchargée.

            # (D) Couverture des compétences : chaque compétence requise est couverte
            #     par un spécialiste interne OU par un sous-traitant (polyvalent).
            for skill in m.required_skills:
                specialists = [
                    assign[(m.name, a)] for a in elig
                    if _has_skill(aud_index[a].skills, skill)
                ]
                # ext[m] >= 1 suffit pour couvrir (un sous-traitant a toutes les compétences)
                model.Add(sum(specialists) + ext[m.name] >= 1)
                n_constraints += 1

        # (E) Disponibilité : si a indisponible en w, il ne peut pas être affecté
        #     à une mission active cette semaine -> assign + active <= 1
        for m in missions:
            for a_name in eligible[m.name]:
                unavail = aud_index[a_name].unavailable_weeks
                for w in unavail:
                    if (m.name, w) in active:
                        model.Add(assign[(m.name, a_name)] + active[(m.name, w)] <= 1)
                        n_constraints += 1

        # (G) Budget par auditeur interne (en semaines) : sum_m L_m * assign <= budget/5
        for a in internal:
            cap_weeks = int(a.budget_days // DAYS_PER_WEEK)
            terms = [
                block_len[m.name] * assign[(m.name, a.name)]
                for m in missions if (m.name, a.name) in assign
            ]
            if terms:
                model.Add(sum(terms) <= cap_weeks)
                n_constraints += 1

        # (F) Un auditeur = une seule mission active par semaine.
        #     On ne crée des variables "work" que là où un conflit est possible
        #     (>= 2 missions candidates pour le même (auditeur, semaine)).
        work_vars: Dict[Tuple[str, str, int], "cp_model.IntVar"] = {}
        # Indexer : pour chaque (auditeur, semaine) -> missions candidates
        aw_missions: Dict[Tuple[str, int], List[str]] = defaultdict(list)
        for m in missions:
            for a_name in eligible[m.name]:
                for w in (b for blkset in blocks[m.name] for b in blkset):
                    if (m.name, w) in active:
                        aw_missions[(a_name, w)].append(m.name)

        for (a_name, w), mnames in aw_missions.items():
            uniq = list(dict.fromkeys(mnames))
            if len(uniq) < 2:
                continue  # pas de conflit possible
            ws = []
            for m_name in uniq:
                wv = model.NewBoolVar(f"work_{m_name}_{a_name}_{w}")
                # work = assign AND active
                model.Add(wv <= assign[(m_name, a_name)])
                model.Add(wv <= active[(m_name, w)])
                model.Add(wv >= assign[(m_name, a_name)] + active[(m_name, w)] - 1)
                work_vars[(m_name, a_name, w)] = wv
                ws.append(wv)
            model.Add(sum(ws) <= 1)
            n_constraints += 1

        # ---- Objectif ----
        # Coefficients volontairement compacts (CP-SAT converge mieux avec de
        # petits entiers) tout en gardant une hiérarchie claire des priorités :
        #   1. minimiser la sous-traitance        (W_EXT, par semaine)
        #   2. charger chaque interne >= seuil 80% (W_UNDERLOAD, par semaine)
        #   3. maximiser l'adéquation compétences  (W_SKILL)
        SKILL_SCALE = 10
        W_EXT = 1000
        W_UNDERLOAD = 50
        W_OPTIN = 150      # préférences opt-in : participation superviseur / chef désigné
        W_SKILL = 1

        # 1. Sous-traitance (semaines) à minimiser
        ext_weeks_terms = [block_len[m.name] * ext[m.name] for m in missions]

        # 1bis. Préférences opt-in (récompense si satisfaites)
        optin_terms = []
        for m in missions:
            if _participates(m) and (m.name, m.supervisor_name) in assign:
                optin_terms.append(assign[(m.name, m.supervisor_name)])
            lead = getattr(m, "mission_lead_name", None)
            if lead and (m.name, lead) in assign:
                optin_terms.append(assign[(m.name, lead)])

        # 2. Adéquation compétences à maximiser
        skill_terms = []
        for m in missions:
            for a_name in eligible[m.name]:
                sc = int(round(_skill_score(aud_index[a_name].skills, m.required_skills) * SKILL_SCALE))
                if sc:
                    skill_terms.append(sc * assign[(m.name, a_name)])

        # 3. Charge minimale (80%) par auditeur interne
        #    load[a]  = nombre de semaines travaillées par a
        #    short[a] = manque (en semaines) sous le seuil de 80% de sa capacité
        #    On pénalise short[a] -> tout le monde >= 80% si le volume de travail
        #    le permet ; sinon on s'en approche au plus près.
        max_total_weeks = sum(block_len[m.name] for m in missions) or 1
        short_vars = []
        for a in internal:
            if a.is_supervisor:
                continue  # le seuil 80% s'applique aux exécutants, pas aux validateurs
            terms = [
                block_len[m.name] * assign[(m.name, a.name)]
                for m in missions if (m.name, a.name) in assign
            ]
            if not terms:
                continue
            cap_weeks = int(a.budget_days // DAYS_PER_WEEK)
            floor_weeks = int(math.ceil(min_load_ratio * cap_weeks))
            lv = model.NewIntVar(0, max_total_weeks, f"load_{a.name}")
            model.Add(lv == sum(terms))
            sv = model.NewIntVar(0, floor_weeks, f"short_{a.name}")
            model.Add(sv >= floor_weeks - lv)  # short >= floor - load (>=0 par domaine)
            short_vars.append(sv)

        model.Maximize(
            W_SKILL * sum(skill_terms)
            + W_OPTIN * sum(optin_terms)
            - W_EXT * sum(ext_weeks_terms)
            - W_UNDERLOAD * sum(short_vars)
        )

        # ---- Limite optionnelle de sous-traitance (contrainte dure) ----
        if max_subcontracting_days is not None and max_subcontracting_days >= 0:
            # On raisonne en semaines (entier) : jours = semaines * 5
            model.Add(
                sum(block_len[m.name] * ext[m.name] for m in missions)
                <= int(max_subcontracting_days // DAYS_PER_WEEK)
            )
            n_constraints += 1

        # ---- Résolution ----
        self.progress_info.update({
            'status': 'Résolution CP-SAT...',
            'phase': 'solve',
            'num_variables': len(model.Proto().variables),
            'num_constraints': len(model.Proto().constraints),
        })

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(time_limit)
        solver.parameters.num_search_workers = os.cpu_count() or 8
        if gap_tolerance and gap_tolerance > 0:
            solver.parameters.relative_gap_limit = gap_tolerance
        solver.parameters.log_search_progress = verbose

        status = solver.Solve(model)

        self.progress_info.update({
            'status': f'Terminé ({solver.StatusName(status)})',
            'phase': 'done',
            'objective_value': solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        })

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            result.messages.append(
                f"Aucune solution trouvée (status CP-SAT: {solver.StatusName(status)})."
            )
            # Si une limite de sous-traitance a provoqué l'infaisabilité,
            # indiquer le minimum réellement nécessaire (re-résolution sans limite).
            if max_subcontracting_days is not None:
                relaxed = CPAuditOptimizer(self.config).optimize(
                    missions, auditors, verbose=False,
                    time_limit=max(10, time_limit // 2),
                    gap_tolerance=gap_tolerance,
                    max_subcontracting_days=None,
                )
                if relaxed.success:
                    min_needed = sum(a.days for a in relaxed.external_allocations)
                    result.messages.append(
                        f"Diagnostic: la limite de sous-traitance ({int(max_subcontracting_days)}j) "
                        f"est trop basse. Minimum nécessaire ≈ {min_needed:.0f}j."
                    )
            self._diagnose_infeasibility(missions, internal, result)
            return result

        # ---- Extraction de la solution ----
        allocations: List[Allocation] = []
        external_allocations: List[Allocation] = []
        ext_counter = 0
        unassigned: List[str] = []

        for m in missions:
            mblocks = blocks[m.name]
            if not mblocks:
                unassigned.append(m.name)
                continue

            # Semaines actives
            chosen_weeks: List[int] = []
            for i, b in enumerate(mblocks):
                if solver.Value(blk[m.name][i]) == 1:
                    chosen_weeks = b
                    break

            # Auditeurs internes affectés
            assigned_internal = [
                a for a in eligible[m.name]
                if solver.Value(assign[(m.name, a)]) == 1
            ]

            # Sous-traitants : noms stables par mission
            n_ext = int(solver.Value(ext[m.name]))
            ext_names = []
            for _ in range(n_ext):
                ext_counter += 1
                ext_names.append(f"Sous-traitant_{ext_counter}")

            # Chef de mission : le chef désigné s'il est dans l'équipe, sinon
            # l'auditeur affecté avec la meilleure adéquation de compétences.
            lead = getattr(m, "mission_lead_name", None)
            chef = lead if (lead in assigned_internal) else None
            if chef is None and assigned_internal:
                chef = max(
                    assigned_internal,
                    key=lambda a: _skill_score(aud_index[a].skills, m.required_skills),
                )

            for w in chosen_weeks:
                for a in assigned_internal:
                    allocations.append(Allocation(
                        mission_name=m.name,
                        auditor_name=a,
                        week=w,
                        days=DAYS_PER_WEEK,
                        is_supervisor=(a == chef),  # marque le CHEF de mission
                    ))
                for ext_name in ext_names:
                    external_allocations.append(Allocation(
                        mission_name=m.name,
                        auditor_name=ext_name,
                        week=w,
                        days=DAYS_PER_WEEK,
                        is_supervisor=False,
                    ))

        result.success = True
        result.allocations = allocations
        result.external_allocations = external_allocations
        result.external_resources_needed = len(external_allocations) > 0
        result.unassigned_missions = unassigned

        elapsed = time.time() - start_time
        ext_days = sum(a.days for a in external_allocations)
        status_name = "optimale" if status == cp_model.OPTIMAL else "réalisable"
        result.messages.append(
            f"Solution {status_name} trouvée en {elapsed:.1f}s (CP-SAT)."
        )
        if ext_days > 0:
            n_ext_res = len(set(a.auditor_name for a in external_allocations))
            result.messages.append(
                f"Sous-traitance: {ext_days:.0f} jours ({n_ext_res} ressource(s))."
            )
        else:
            result.messages.append("Solution 100% interne (aucune sous-traitance).")

        # Bilan de la charge minimale (objectif 80%)
        weeks_by_aud: Dict[str, int] = defaultdict(int)
        for al in allocations:
            weeks_by_aud[al.auditor_name] += 1  # 1 semaine par allocation (5j)
        n_below = 0
        worst = 100.0
        for a in internal:
            if a.is_supervisor:
                continue  # validateurs exclus du calcul de charge d'exécution
            cap_weeks = a.budget_days / DAYS_PER_WEEK
            if cap_weeks <= 0:
                continue
            ratio = (weeks_by_aud.get(a.name, 0)) / cap_weeks
            worst = min(worst, ratio)
            if ratio + 1e-9 < min_load_ratio:
                n_below += 1
        if n_below == 0:
            result.messages.append(
                f"Charge: tous les auditeurs internes sont chargés à ≥ {min_load_ratio*100:.0f}%."
            )
        else:
            result.messages.append(
                f"Charge: {n_below} auditeur(s) sous {min_load_ratio*100:.0f}% "
                f"(minimum atteint: {worst*100:.0f}%) — volume de travail insuffisant "
                f"pour ce seuil."
            )

        if unassigned:
            result.messages.append(
                f"Attention: {len(unassigned)} mission(s) sans fenêtre de planning valide."
            )

        return result

    # ------------------------------------------------------------------ #
    def _diagnose_infeasibility(self, missions, internal, result: OptimizationResult):
        """Ajoute des messages de diagnostic en cas d'échec."""
        total_mission_days = sum(m.budget_days for m in missions)
        total_capacity = sum(a.budget_days for a in internal)
        if total_mission_days > total_capacity:
            result.messages.append(
                f"Diagnostic: capacité interne insuffisante "
                f"({total_mission_days:.0f}j requis / {total_capacity:.0f}j disponibles)."
            )
        for m in missions:
            for skill in m.required_skills:
                n = sum(1 for a in internal if skill in a.skills)
                if n == 0:
                    result.messages.append(
                        f"Diagnostic: aucune ressource interne avec la compétence "
                        f"'{skill.value}' (mission '{m.name}')."
                    )
