"""
Application Streamlit pour l'optimisation du staffing des missions d'audit.
"""

import streamlit as st
import pandas as pd
import json
import os
import time
import threading
import logging
from io import BytesIO
from typing import List, Optional
from datetime import date, datetime

# Supprimer le warning ScriptRunContext de Streamlit
logging.getLogger('streamlit.runtime.scriptrunner_utils.script_run_context').setLevel(logging.ERROR)

from models import (
    Auditor, Mission, PlanningConfig, Skill, PeriodType,
    get_weeks_for_period, AuditorProfile, AuditorGrade, AuditorStatus,
    Unavailability, UnavailabilityType, OptimizationResult
)
from optimizer import AuditStaffingOptimizer
from genetic_optimizer import GeneticAuditOptimizer
from excel_handler import ExcelReader, ExcelWriter, create_template_excel
from constraints import parse_unavailable_periods, ConstraintValidator
from utils import (
    validate_input_data, calculate_utilization_stats,
    analyze_feasibility, format_week_range,
    build_supervisor_planning, calculate_supervisor_stats
)
from logger import logger
from auditor_manager import auditor_manager
from paths import get_base_dir

# Chemin du fichier de données (à côté de l'exécutable si empaqueté)
DATA_DIR = os.path.join(get_base_dir(), "data")
AUDITORS_FILE = os.path.join(DATA_DIR, "auditors.json")
SUPERVISORS_FILE = os.path.join(DATA_DIR, "supervisors.json")
PLANNING_FILE = os.path.join(DATA_DIR, "current_planning.json")


def load_auditors_from_file() -> List[Auditor]:
    """Charge les auditeurs depuis le fichier JSON."""
    if not os.path.exists(AUDITORS_FILE):
        return []
    try:
        with open(AUDITORS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        auditors = []
        for item in data:
            skills = {Skill(s) for s in item.get('skills', ['Généraliste'])}
            auditor = Auditor(
                name=item['name'],
                budget_days=item['budget_days'],
                skills=skills,
                unavailable_weeks=set(item.get('unavailable_weeks', []))
            )
            auditors.append(auditor)
        return auditors
    except Exception as e:
        st.warning(f"Erreur lors du chargement des auditeurs: {e}")
        return []


def save_auditors_to_file(auditors: List[Auditor]) -> bool:
    """Sauvegarde les auditeurs dans le fichier JSON."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        data = []
        for a in auditors:
            data.append({
                'name': a.name,
                'budget_days': a.budget_days,
                'skills': [s.value for s in a.skills],
                'unavailable_weeks': list(a.unavailable_weeks)
            })
        with open(AUDITORS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"Erreur lors de la sauvegarde: {e}")
        return False


def load_supervisors_from_file() -> List[dict]:
    """Charge les superviseurs depuis le fichier JSON."""
    if not os.path.exists(SUPERVISORS_FILE):
        return []
    try:
        with open(SUPERVISORS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"Erreur lors du chargement des superviseurs: {e}")
        return []


def save_supervisors_to_file(supervisors: List[dict]) -> bool:
    """Sauvegarde les superviseurs dans le fichier JSON."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(SUPERVISORS_FILE, 'w', encoding='utf-8') as f:
            json.dump(supervisors, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"Erreur lors de la sauvegarde: {e}")
        return False


def save_planning_auto(result: OptimizationResult, missions: List[Mission],
                       auditors: List[Auditor], year: int) -> bool:
    """Sauvegarde automatique du planning courant."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        export_data = {
            'version': '2.0',
            'auto_saved': True,
            'created_at': datetime.now().isoformat(),
            'year': year,
            'result': result.to_dict(),
            'missions': [
                {
                    'name': m.name,
                    'budget_days': m.budget_days,
                    'num_auditors': m.num_auditors,
                    'period_type': m.period_type.value if m.period_type else 'Trimestre',
                    'period_value': m.period_value,
                    'required_skills': [s.value for s in m.required_skills] if m.required_skills else ['Généraliste'],
                    'allowed_weeks': list(m.allowed_weeks),
                    'supervisor_name': m.supervisor_name
                }
                for m in missions
            ],
            'auditors': [
                {
                    'name': a.name,
                    'budget_days': a.budget_days,
                    'skills': [s.value for s in a.skills],
                    'unavailable_weeks': list(a.unavailable_weeks),
                    'is_external': a.is_external,
                    'is_supervisor': a.is_supervisor
                }
                for a in auditors
            ]
        }

        with open(PLANNING_FILE, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        logger.info("Planning", f"Sauvegarde automatique: {len(result.allocations)} allocations")
        return True
    except Exception as e:
        logger.error("Planning", f"Erreur sauvegarde auto: {e}")
        return False


def load_planning_auto() -> Optional[dict]:
    """Charge le dernier planning sauvegardé automatiquement."""
    if not os.path.exists(PLANNING_FILE):
        return None
    try:
        with open(PLANNING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error("Planning", f"Erreur chargement planning: {e}")
        return None


def get_planning_info() -> Optional[dict]:
    """Retourne les infos du planning sauvegardé (sans le charger complètement)."""
    if not os.path.exists(PLANNING_FILE):
        return None
    try:
        with open(PLANNING_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {
            'created_at': data.get('created_at'),
            'year': data.get('year'),
            'num_allocations': len(data.get('result', {}).get('allocations', [])),
            'num_external': len(data.get('result', {}).get('external_allocations', [])),
            'num_missions': len(data.get('missions', [])),
            'num_auditors': len(data.get('auditors', []))
        }
    except Exception:
        return None


def _load_planning_from_auto():
    """Charge le planning depuis le fichier auto-sauvegardé."""
    planning_data = load_planning_auto()
    if not planning_data:
        return False

    try:
        # Charger les missions
        missions = []
        for m in planning_data.get('missions', []):
            # Gérer les deux formats: required_skill (ancien) et required_skills (nouveau)
            skills = set()
            if m.get('required_skill'):
                skill = Skill.from_string(m['required_skill'])
                if skill:
                    skills.add(skill)
            elif m.get('required_skills'):
                for s in m['required_skills']:
                    skill = Skill.from_string(s)
                    if skill:
                        skills.add(skill)
            if not skills:
                skills = {Skill.GENERALISTE}

            # Gérer le period_type
            period_type = PeriodType.TRIMESTRE
            if m.get('period_type'):
                if m['period_type'] == 'Mois':
                    period_type = PeriodType.MOIS
                elif m['period_type'] == 'Semaine':
                    period_type = PeriodType.SEMAINE

            missions.append(Mission(
                name=m['name'],
                budget_days=m['budget_days'],
                num_auditors=m['num_auditors'],
                period_type=period_type,
                period_value=m.get('period_value', ''),
                required_skills=skills,
                allowed_weeks=set(m.get('allowed_weeks', [])),
                supervisor_name=m.get('supervisor_name')
            ))

        # Charger les auditeurs
        auditors = []
        for a in planning_data.get('auditors', []):
            skills = {Skill.from_string(s) for s in a.get('skills', []) if Skill.from_string(s)}
            auditors.append(Auditor(
                name=a['name'],
                budget_days=a['budget_days'],
                skills=skills if skills else {Skill.GENERALISTE},
                unavailable_weeks=set(a.get('unavailable_weeks', [])),
                is_external=a.get('is_external', False),
                is_supervisor=a.get('is_supervisor', False)
            ))

        # Charger le résultat
        result_data = planning_data.get('result', {})
        loaded_result = OptimizationResult.from_dict(result_data)

        # Mettre à jour session_state
        st.session_state.missions = missions
        st.session_state.auditors = auditors
        st.session_state.result = loaded_result
        st.session_state.year = planning_data.get('year', datetime.now().year)

        logger.info("Planning", f"Planning chargé: {len(missions)} missions, {len(auditors)} auditeurs")
        return True
    except Exception as e:
        logger.error("Planning", f"Erreur chargement: {e}")
        return False


def detect_period_type(period_value: str) -> PeriodType:
    """Détecte automatiquement le type de période à partir de la valeur."""
    if not period_value:
        return PeriodType.TRIMESTRE

    period_lower = period_value.lower().strip()

    # Trimestre: Q1, Q2, T1, etc.
    if period_lower.startswith("q") or period_lower.startswith("t") and len(period_lower) <= 3:
        return PeriodType.TRIMESTRE

    # Mois: noms de mois
    month_keywords = [
        "jan", "fév", "fev", "mar", "avr", "mai", "jun", "jui",
        "aoû", "aou", "sep", "oct", "nov", "déc", "dec"
    ]
    if any(m in period_lower for m in month_keywords):
        return PeriodType.MOIS

    # Semaine: S10, S10-S20, 10-20, etc.
    if period_lower.startswith("s") or period_lower.replace("-", "").replace(" ", "").isdigit():
        return PeriodType.SEMAINE

    # Par défaut: trimestre
    return PeriodType.TRIMESTRE


# Configuration de la page
st.set_page_config(
    page_title="Optimisation Staffing Audit",
    page_icon="📊",
    layout="wide"
)


def init_session_state():
    """Initialise les variables de session."""
    if 'missions' not in st.session_state:
        st.session_state.missions = []
    if 'auditors' not in st.session_state:
        st.session_state.auditors = load_auditors_from_file()
    if 'supervisors' not in st.session_state:
        st.session_state.supervisors = load_supervisors_from_file()
    if 'result' not in st.session_state:
        st.session_state.result = None
    if 'year' not in st.session_state:
        st.session_state.year = datetime.now().year  # Année courante par défaut
    if 'editing_auditor' not in st.session_state:
        st.session_state.editing_auditor = None
    if 'editing_supervisor' not in st.session_state:
        st.session_state.editing_supervisor = None
    if 'editing_mission' not in st.session_state:
        st.session_state.editing_mission = None
    if 'optimizer_type' not in st.session_state:
        st.session_state.optimizer_type = "CPSAT"
    if 'min_load_ratio' not in st.session_state:
        st.session_state.min_load_ratio = 0.80
    if 'unlimited_subcontracting' not in st.session_state:
        st.session_state.unlimited_subcontracting = True
    if 'selected_auditor' not in st.session_state:
        st.session_state.selected_auditor = None
    if 'editing_profile' not in st.session_state:
        st.session_state.editing_profile = False
    if 'current_week' not in st.session_state:
        st.session_state.current_week = datetime.now().isocalendar()[1]


def main():
    """Point d'entrée principal de l'application."""
    init_session_state()

    st.title("Optimisation du Staffing des Missions d'Audit")
    st.markdown("---")

    # Sidebar pour la configuration
    with st.sidebar:
        st.header("Configuration")

        st.session_state.year = st.number_input(
            "Année de planification",
            min_value=2020,
            max_value=2030,
            value=st.session_state.year
        )

        st.markdown("---")
        st.subheader("Contraintes automatiques")
        st.info(
            "**Semaines bloquées:**\n"
            "- Août (S31-S35)\n"
            "- 15-30 décembre (S51-S52)"
        )

        st.markdown("---")
        st.subheader("Aide")
        st.markdown("""
        **Étapes:**
        1. Charger un fichier Excel ou saisir manuellement
        2. Vérifier les données
        3. Lancer l'optimisation
        4. Télécharger les résultats
        """)

        # Télécharger le template
        st.markdown("---")
        if st.button("Télécharger Template Excel"):
            buffer = BytesIO()
            create_template_excel_buffer(buffer)
            st.download_button(
                label="Télécharger",
                data=buffer.getvalue(),
                file_name="template_audit_planning.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    # Onglets principaux
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "1. Import des données",
        "2. Configuration",
        "3. Optimisation",
        "4. Résultats",
        "5. Auditeurs",
        "6. Replanification",
        "7. Générateur de scénario"
    ])

    # Tab 1: Import des données
    with tab1:
        render_import_tab()

    # Tab 2: Configuration
    with tab2:
        render_config_tab()

    # Tab 3: Optimisation
    with tab3:
        render_optimization_tab()

    # Tab 4: Résultats
    with tab4:
        render_results_tab()

    # Tab 5: Gestion des auditeurs
    with tab5:
        render_auditors_tab()

    # Tab 6: Replanification
    with tab6:
        render_replanification_tab()

    # Tab 7: Générateur de scénario
    with tab7:
        render_scenario_generator_tab()


def render_scenario_generator_tab():
    """Onglet de génération de scénarios de test paramétrables."""
    import generate_demo_data as gen

    st.header("Générateur de scénario")
    st.markdown(
        "Génère un scénario réaliste (auditeurs avec vrais noms, congés d'août + "
        "maladie, missions aléatoires, goulot « Modèle ») que vous pouvez "
        "**télécharger en Excel** ou **charger directement** dans l'application."
    )

    max_names = len(gen.PRENOMS) * len(gen.NOMS)

    col1, col2, col3 = st.columns(3)
    with col1:
        n_auditeurs = st.slider(
            "Nombre d'auditeurs", min_value=3, max_value=min(200, max_names),
            value=int(st.session_state.get('gen_n_auditeurs', 40)), step=1,
            help="Nombre total d'auditeurs (superviseurs inclus)."
        )
        st.session_state.gen_n_auditeurs = n_auditeurs
    with col2:
        n_superviseurs = st.slider(
            "Nombre de superviseurs", min_value=0, max_value=max(1, n_auditeurs - 1),
            value=int(min(st.session_state.get('gen_n_superviseurs', 3), n_auditeurs - 1)),
            step=1,
            help="Superviseurs/validateurs (choisis parmi les auditeurs)."
        )
        st.session_state.gen_n_superviseurs = n_superviseurs
    with col3:
        n_missions = st.slider(
            "Nombre de missions", min_value=1, max_value=400,
            value=int(st.session_state.get('gen_n_missions', 90)), step=1,
            help="Nombre de missions à générer."
        )
        st.session_state.gen_n_missions = n_missions

    col_seed, col_year = st.columns(2)
    with col_seed:
        seed = st.number_input(
            "Graine aléatoire (reproductibilité)", min_value=0, max_value=999999,
            value=int(st.session_state.get('gen_seed', gen.SEED)), step=1
        )
        st.session_state.gen_seed = seed
    with col_year:
        st.caption(
            f"Exécutants = auditeurs − superviseurs = **{n_auditeurs - n_superviseurs}**. "
            "Le seuil de charge minimale s'applique à ces exécutants."
        )

    st.markdown("---")
    if st.button("Générer le scénario", type="primary"):
        try:
            auditor_rows, mission_rows, supervisors = gen.generate(
                n_auditors=n_auditeurs,
                n_supervisors=n_superviseurs,
                n_missions=n_missions,
                seed=int(seed),
            )
            # Écriture dans un buffer Excel en mémoire
            buffer = BytesIO()
            gen.write_excel(buffer, auditor_rows, mission_rows)
            buffer.seek(0)
            st.session_state.generated_scenario_bytes = buffer.getvalue()
            st.session_state.generated_scenario_meta = {
                "auditeurs": len(auditor_rows),
                "superviseurs": len(supervisors),
                "missions": len(mission_rows),
                "budget": sum(m["Budget (jours)"] for m in mission_rows),
                "capacite_exec": sum(a["Budget (jours)"] for a in auditor_rows
                                     if a["Peut Superviser"] != "Oui"),
            }
            st.success("Scénario généré avec succès.")
        except Exception as e:
            st.error(f"Erreur lors de la génération : {e}")

    # Affichage du résultat généré (persiste tant qu'on ne régénère pas)
    meta = st.session_state.get('generated_scenario_meta')
    data = st.session_state.get('generated_scenario_bytes')
    if meta and data:
        st.markdown("### Scénario généré")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Auditeurs", meta["auditeurs"])
        c2.metric("Superviseurs", meta["superviseurs"])
        c3.metric("Missions", meta["missions"])
        charge = (100 * meta["budget"] / meta["capacite_exec"]) if meta["capacite_exec"] else 0
        c4.metric("Charge exécutants", f"{charge:.0f}%")

        col_dl, col_load = st.columns(2)
        with col_dl:
            st.download_button(
                "Télécharger le fichier Excel",
                data=data,
                file_name=f"scenario_{meta['auditeurs']}a_{meta['superviseurs']}s_{meta['missions']}m.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with col_load:
            if st.button("Charger ce scénario dans l'application"):
                try:
                    reader = ExcelReader(BytesIO(data))
                    sheets = reader.read_file()
                    missions = reader.parse_missions(
                        sheets["Missions"], col_name="Nom Mission", col_period="Période",
                        col_period_type=None, col_budget="Budget (jours)",
                        col_supervisor="Superviseur", col_num_auditors="Nb Auditeurs",
                        col_skills="Compétences Requises", year=st.session_state.year,
                    )
                    auditors = reader.parse_auditors(
                        sheets["Auditeurs"], col_name="Nom", col_budget="Budget (jours)",
                        col_skills="Compétences", col_unavailable="Indisponibilités",
                        col_is_supervisor="Peut Superviser", year=st.session_state.year,
                    )
                    st.session_state.missions = missions
                    st.session_state.auditors = auditors
                    st.success(
                        f"Chargé : {len(missions)} missions et {len(auditors)} auditeurs. "
                        "Allez à l'onglet « Optimisation » pour lancer le solveur."
                    )
                except Exception as e:
                    st.error(f"Erreur lors du chargement : {e}")


def render_import_tab():
    """Onglet d'import des données."""
    st.header("Import des données")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Charger un fichier Excel")
        uploaded_file = st.file_uploader(
            "Sélectionner un fichier Excel",
            type=['xlsx', 'xls'],
            help=(
                "Le fichier peut contenir une feuille 'Missions' ou toute autre feuille "
                "avec les colonnes Mission / Période / Superviseur / Budget / Skill. "
                "La feuille 'Auditeurs' est optionnelle."
            )
        )

        if uploaded_file:
            try:
                reader = ExcelReader(uploaded_file)
                sheets = reader.read_file()

                st.success(f"Fichier chargé avec {len(sheets)} feuille(s)")
                st.write("**Feuilles disponibles:**", list(sheets.keys()))

                # ==============================
                # Configuration Missions
                # ==============================
                st.markdown("---")
                st.subheader("Configuration des colonnes - Missions")

                if not sheets:
                    st.error("Aucune feuille trouvée dans le fichier Excel.")
                    return

                sheet_names = list(sheets.keys())
                # Par défaut: 'Missions' si présente, sinon première feuille
                default_mission_idx = sheet_names.index("Missions") if "Missions" in sheet_names else 0
                missions_sheet_name = st.selectbox(
                    "Feuille contenant les missions",
                    sheet_names,
                    index=default_mission_idx
                )

                df_missions = sheets[missions_sheet_name]
                cols = list(df_missions.columns)

                # Nom
                col_name = st.selectbox("Colonne Nom Mission", cols, index=0)

                # Période
                if "Période" in cols:
                    period_default_idx = cols.index("Période")
                else:
                    period_default_idx = min(1, len(cols) - 1)
                col_period = st.selectbox(
                    "Colonne Période",
                    cols,
                    index=period_default_idx
                )

                # Budget
                if "Budget (jours)" in cols:
                    budget_default_idx = cols.index("Budget (jours)")
                elif "Budget" in cols:
                    budget_default_idx = cols.index("Budget")
                else:
                    budget_default_idx = min(3, len(cols) - 1)
                col_budget = st.selectbox(
                    "Colonne Budget (jours)",
                    cols,
                    index=budget_default_idx
                )

                # Superviseur
                if "Superviseur" in cols:
                    sup_default_idx = cols.index("Superviseur")
                else:
                    sup_default_idx = 0
                col_supervisor = st.selectbox(
                    "Colonne Superviseur",
                    cols,
                    index=sup_default_idx
                )

                # Nb Auditeurs (optionnel)
                num_auditors_options = ["(Constante = 1)"] + cols
                default_num_aud_idx = 0
                if "Nb Auditeurs" in cols:
                    default_num_aud_idx = num_auditors_options.index("Nb Auditeurs")

                col_num_auditors_label = st.selectbox(
                    "Colonne Nb Auditeurs",
                    num_auditors_options,
                    index=default_num_aud_idx
                )
                col_num_auditors = None if col_num_auditors_label == "(Constante = 1)" else col_num_auditors_label

                # Compétences requises
                col_skills_mission = st.selectbox(
                    "Colonne Compétences Requises",
                    ["(Aucune)"] + cols,
                    index=(["(Aucune)"] + cols).index("Compétences Requises")
                    if "Compétences Requises" in cols else 0
                )

                # ==============================
                # Configuration Auditeurs (optionnelle)
                # ==============================
                st.markdown("---")
                st.subheader("Configuration des colonnes - Auditeurs")

                has_auditors_sheet = "Auditeurs" in sheets
                df_auditors = None
                if has_auditors_sheet:
                    df_auditors = sheets["Auditeurs"]
                    cols_aud = list(df_auditors.columns)

                    col_aud_name = st.selectbox("Colonne Nom Auditeur", cols_aud, index=0)
                    col_aud_budget = st.selectbox(
                        "Colonne Budget Auditeur (jours)",
                        cols_aud,
                        index=cols_aud.index("Budget (jours)") if "Budget (jours)" in cols_aud else 1
                    )
                    col_aud_skills = st.selectbox(
                        "Colonne Compétences",
                        cols_aud,
                        index=cols_aud.index("Compétences") if "Compétences" in cols_aud else 2
                    )
                    col_aud_unavail = st.selectbox(
                        "Colonne Indisponibilités",
                        ["(Aucune)"] + cols_aud,
                        index=0
                    )
                    col_aud_supervisor = st.selectbox(
                        "Colonne Peut Superviser",
                        ["(Aucune)"] + cols_aud,
                        index=0
                    )
                else:
                    st.info(
                        "Aucune feuille 'Auditeurs' détectée. "
                        "Vous pourrez ajouter les auditeurs manuellement dans l'onglet 'Configuration'."
                    )

                # ==============================
                # Import
                # ==============================
                if st.button("Importer les données", type="primary"):
                    try:
                        missions = reader.parse_missions(
                            df_missions,
                            col_name=col_name,
                            col_period=col_period,
                            col_period_type=None,  # Auto-détection
                            col_budget=col_budget,
                            col_supervisor=col_supervisor,
                            col_num_auditors=col_num_auditors,
                            col_skills=col_skills_mission if col_skills_mission != "(Aucune)" else None,
                            year=st.session_state.year
                        )

                        auditors: List[Auditor] = []
                        if has_auditors_sheet and df_auditors is not None:
                            auditors = reader.parse_auditors(
                                df_auditors,
                                col_name=col_aud_name,
                                col_budget=col_aud_budget,
                                col_skills=col_aud_skills,
                                col_unavailable=col_aud_unavail if col_aud_unavail != "(Aucune)" else None,
                                col_is_supervisor=col_aud_supervisor if col_aud_supervisor != "(Aucune)" else None,
                                year=st.session_state.year
                            )

                        st.session_state.missions = missions
                        st.session_state.auditors = auditors

                        st.success(
                            f"Import réussi: {len(missions)} missions, {len(auditors)} auditeurs"
                        )

                    except Exception as e:
                        st.error(f"Erreur lors de l'import: {e}")

            except Exception as e:
                st.error(f"Erreur lors de la lecture du fichier: {e}")

    with col2:
        st.subheader("Aperçu des données importées")

        if st.session_state.missions:
            st.write(f"**{len(st.session_state.missions)} missions importées:**")
            mission_data = []
            for m in st.session_state.missions:
                mission_data.append({
                    "Nom": m.name,
                    "Budget (j)": m.budget_days,
                    "Période": m.period_value,
                    "Superviseur": m.supervisor_name or "-",
                    "Nb Auditeurs": m.num_auditors,
                    "Semaines": format_week_range(m.allowed_weeks)
                })
            st.dataframe(pd.DataFrame(mission_data), use_container_width=True)

        if st.session_state.auditors:
            st.write(f"**{len(st.session_state.auditors)} auditeurs importés:**")
            auditor_data = []
            for a in st.session_state.auditors:
                auditor_data.append({
                    "Nom": a.name,
                    "Budget (j)": a.budget_days,
                    "Compétences": ", ".join(s.value for s in a.skills),
                    "Indisponibilités": format_week_range(a.unavailable_weeks) if a.unavailable_weeks else "-"
                })
            st.dataframe(pd.DataFrame(auditor_data), use_container_width=True)


def render_config_tab():
    """Onglet de configuration manuelle."""
    st.header("Configuration manuelle")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Ajouter une mission")

        with st.form("add_mission"):
            mission_name = st.text_input("Nom de la mission")
            mission_budget = st.number_input("Budget (jours)", min_value=1, value=10)
            num_auditors = st.number_input("Nombre d'auditeurs requis", min_value=1, value=1)

            period_value = st.text_input(
                "Période",
                placeholder="Ex: Q1, Janvier-Mars, S10-S20",
                help="Type détecté auto: Q1/T1=Trimestre, Janvier/Mars=Mois, S10-S20=Semaine"
            )

            skills_required = st.multiselect(
                "Compétences requises",
                ["IT", "Modèle", "Assurance", "Généraliste"],
                default=["Généraliste"]
            )

            # Superviseur : choix parmi les superviseurs enregistrés
            supervisor_names = [s['name'] for s in st.session_state.supervisors]
            supervisor_choice = st.selectbox(
                "Superviseur",
                ["(Aucun)"] + supervisor_names
            )

            if st.form_submit_button("Ajouter la mission"):
                if not mission_name:
                    st.error("Le nom de la mission est obligatoire")
                    st.stop()

                if not period_value:
                    st.error("La période est obligatoire")
                    st.stop()

                # Déterminer le PeriodType automatiquement
                pt = detect_period_type(period_value)

                skills = set()
                for s in skills_required:
                    skill = Skill.from_string(s)
                    if skill:
                        skills.add(skill)

                # Calcul des semaines autorisées
                allowed_weeks = get_weeks_for_period(pt, period_value, st.session_state.year)

                # Déterminer le superviseur
                if supervisor_choice != "(Aucun)":
                    supervisor = supervisor_choice
                else:
                    supervisor = None

                mission = Mission(
                    name=mission_name,
                    budget_days=mission_budget,
                    period_type=pt,
                    period_value=period_value,
                    required_skills=skills,
                    supervisor_name=supervisor,
                    num_auditors=num_auditors,
                    allowed_weeks=allowed_weeks
                )

                st.session_state.missions.append(mission)
                st.success(f"Mission '{mission_name}' ajoutée")
                st.rerun()

    with col2:
        st.subheader("Ajouter un auditeur")

        with st.form("add_auditor"):
            auditor_name = st.text_input("Nom de l'auditeur")
            auditor_budget = st.number_input("Budget annuel (jours)", min_value=1, value=205)

            auditor_skills = st.multiselect(
                "Compétences",
                ["IT", "Modèle", "Assurance", "Généraliste"],
                default=["Généraliste"]
            )

            unavailable = st.text_input(
                "Indisponibilités (ex: S30-S35, août)",
                ""
            )

            if st.form_submit_button("Ajouter l'auditeur"):
                skills = set()
                for s in auditor_skills:
                    skill = Skill.from_string(s)
                    if skill:
                        skills.add(skill)

                unavailable_weeks = parse_unavailable_periods(
                    unavailable, st.session_state.year
                ) if unavailable else set()

                auditor = Auditor(
                    name=auditor_name,
                    budget_days=auditor_budget,
                    skills=skills,
                    unavailable_weeks=unavailable_weeks
                )

                st.session_state.auditors.append(auditor)
                save_auditors_to_file(st.session_state.auditors)
                st.success(f"Auditeur '{auditor_name}' ajouté et sauvegardé")
                st.rerun()

    # Affichage et gestion des données existantes
    st.markdown("---")
    st.subheader("Données actuelles")

    col3, col4 = st.columns(2)

    with col3:
        st.write(f"**Missions ({len(st.session_state.missions)})**")
        for i, m in enumerate(st.session_state.missions):
            # Indicateur si semaines vides
            weeks_status = "" if m.allowed_weeks else " ⚠️"
            with st.expander(f"{m.name} ({m.budget_days}j){weeks_status}"):
                # Mode édition
                if st.session_state.editing_mission == i:
                    with st.form(f"edit_mission_{i}"):
                        new_name = st.text_input("Nom", value=m.name)
                        new_budget = st.number_input("Budget (jours)", min_value=1, value=int(m.budget_days))
                        new_num_auditors = st.number_input("Nb auditeurs", min_value=1, value=m.num_auditors)

                        new_period_value = st.text_input(
                            "Période (ex: Q1, Janvier-Mars, S10-S20)",
                            value=m.period_value,
                            help="Type détecté auto: Q1/T1=Trimestre, Janvier/Mars=Mois, S10-S20=Semaine"
                        )

                        new_skills = st.multiselect(
                            "Compétences",
                            ["IT", "Modèle", "Assurance", "Généraliste"],
                            default=[s.value for s in m.required_skills]
                        )

                        supervisor_names = ["(Aucun)"] + [s['name'] for s in st.session_state.supervisors]
                        current_sup_idx = 0
                        if m.supervisor_name and m.supervisor_name in supervisor_names:
                            current_sup_idx = supervisor_names.index(m.supervisor_name)
                        new_supervisor = st.selectbox("Superviseur", supervisor_names, index=current_sup_idx)

                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.form_submit_button("Sauvegarder"):
                                # Déterminer le type de période automatiquement
                                pt = detect_period_type(new_period_value)

                                # Calculer les semaines
                                new_allowed_weeks = get_weeks_for_period(pt, new_period_value, st.session_state.year)

                                # Compétences
                                skills = set()
                                for s in new_skills:
                                    skill = Skill.from_string(s)
                                    if skill:
                                        skills.add(skill)

                                st.session_state.missions[i] = Mission(
                                    name=new_name,
                                    budget_days=new_budget,
                                    period_type=pt,
                                    period_value=new_period_value,
                                    required_skills=skills if skills else {Skill.GENERALISTE},
                                    supervisor_name=new_supervisor if new_supervisor != "(Aucun)" else None,
                                    num_auditors=new_num_auditors,
                                    allowed_weeks=new_allowed_weeks
                                )
                                st.session_state.editing_mission = None
                                st.success(f"Mission '{new_name}' modifiée")
                                st.rerun()
                        with col_cancel:
                            if st.form_submit_button("Annuler"):
                                st.session_state.editing_mission = None
                                st.rerun()
                else:
                    # Mode affichage
                    st.write(f"- Période: {m.period_value}")
                    st.write(f"- Semaines: {format_week_range(m.allowed_weeks) if m.allowed_weeks else '⚠️ Aucune'}")
                    st.write(f"- Superviseur: {m.supervisor_name or 'Non défini'}")
                    st.write(f"- Auditeurs requis: {m.num_auditors}")
                    st.write(f"- Compétences: {', '.join(s.value for s in m.required_skills)}")

                    col_edit, col_del = st.columns(2)
                    with col_edit:
                        if st.button("Modifier", key=f"edit_mission_{i}"):
                            st.session_state.editing_mission = i
                            st.rerun()
                    with col_del:
                        if st.button("Supprimer", key=f"del_mission_{i}"):
                            st.session_state.missions.pop(i)
                            st.rerun()

    with col4:
        st.write(f"**Auditeurs ({len(st.session_state.auditors)})**")
        for i, a in enumerate(st.session_state.auditors):
            with st.expander(f"{a.name} ({a.budget_days}j)"):
                # Mode édition
                if st.session_state.editing_auditor == i:
                    with st.form(f"edit_auditor_{i}"):
                        new_name = st.text_input("Nom", value=a.name)
                        new_budget = st.number_input("Budget (jours)", min_value=1, value=int(a.budget_days))
                        new_skills = st.multiselect(
                            "Compétences",
                            ["IT", "Modèle", "Assurance", "Généraliste"],
                            default=[s.value for s in a.skills]
                        )
                        new_unavail = st.text_input(
                            "Indisponibilités",
                            value=format_week_range(a.unavailable_weeks) if a.unavailable_weeks else ""
                        )

                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.form_submit_button("Sauvegarder"):
                                skills = set()
                                for s in new_skills:
                                    skill = Skill.from_string(s)
                                    if skill:
                                        skills.add(skill)
                                unavailable_weeks = parse_unavailable_periods(
                                    new_unavail, st.session_state.year
                                ) if new_unavail and new_unavail != "-" else set()
                                st.session_state.auditors[i] = Auditor(
                                    name=new_name,
                                    budget_days=new_budget,
                                    skills=skills,
                                    unavailable_weeks=unavailable_weeks
                                )
                                save_auditors_to_file(st.session_state.auditors)
                                st.session_state.editing_auditor = None
                                st.success(f"Auditeur '{new_name}' modifié")
                                st.rerun()
                        with col_cancel:
                            if st.form_submit_button("Annuler"):
                                st.session_state.editing_auditor = None
                                st.rerun()
                else:
                    # Mode affichage
                    st.write(f"- Compétences: {', '.join(s.value for s in a.skills)}")
                    if a.unavailable_weeks:
                        st.write(f"- Indisponibilités: {format_week_range(a.unavailable_weeks)}")

                    col_edit, col_del = st.columns(2)
                    with col_edit:
                        if st.button("Modifier", key=f"edit_auditor_{i}"):
                            st.session_state.editing_auditor = i
                            st.rerun()
                    with col_del:
                        if st.button("Supprimer", key=f"del_auditor_{i}"):
                            st.session_state.auditors.pop(i)
                            save_auditors_to_file(st.session_state.auditors)
                            st.rerun()

    # Section Superviseurs
    st.markdown("---")
    st.subheader("Superviseurs")

    col5, col6 = st.columns(2)

    with col5:
        st.write("**Ajouter un superviseur**")
        with st.form("add_supervisor"):
            sup_name = st.text_input("Nom du superviseur")
            sup_email = st.text_input("Email (optionnel)")
            sup_department = st.text_input("Département (optionnel)")

            if st.form_submit_button("Ajouter le superviseur"):
                if sup_name.strip():
                    new_sup = {
                        'name': sup_name.strip(),
                        'email': sup_email.strip() if sup_email else "",
                        'department': sup_department.strip() if sup_department else ""
                    }
                    st.session_state.supervisors.append(new_sup)
                    save_supervisors_to_file(st.session_state.supervisors)
                    st.success(f"Superviseur '{sup_name}' ajouté")
                    st.rerun()
                else:
                    st.error("Le nom du superviseur est requis")

    with col6:
        st.write(f"**Superviseurs enregistrés ({len(st.session_state.supervisors)})**")
        for i, sup in enumerate(st.session_state.supervisors):
            with st.expander(f"{sup['name']}"):
                # Mode édition
                if st.session_state.editing_supervisor == i:
                    with st.form(f"edit_supervisor_{i}"):
                        new_sup_name = st.text_input("Nom", value=sup['name'])
                        new_sup_email = st.text_input("Email", value=sup.get('email', ''))
                        new_sup_dept = st.text_input("Département", value=sup.get('department', ''))

                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            if st.form_submit_button("Sauvegarder"):
                                st.session_state.supervisors[i] = {
                                    'name': new_sup_name.strip(),
                                    'email': new_sup_email.strip(),
                                    'department': new_sup_dept.strip()
                                }
                                save_supervisors_to_file(st.session_state.supervisors)
                                st.session_state.editing_supervisor = None
                                st.success(f"Superviseur '{new_sup_name}' modifié")
                                st.rerun()
                        with col_cancel:
                            if st.form_submit_button("Annuler"):
                                st.session_state.editing_supervisor = None
                                st.rerun()
                else:
                    # Mode affichage
                    if sup.get('email'):
                        st.write(f"- Email: {sup['email']}")
                    if sup.get('department'):
                        st.write(f"- Département: {sup['department']}")

                    col_edit, col_del = st.columns(2)
                    with col_edit:
                        if st.button("Modifier", key=f"edit_sup_{i}"):
                            st.session_state.editing_supervisor = i
                            st.rerun()
                    with col_del:
                        if st.button("Supprimer", key=f"del_sup_{i}"):
                            st.session_state.supervisors.pop(i)
                            save_supervisors_to_file(st.session_state.supervisors)
                            st.rerun()


def render_optimization_tab():
    """Onglet d'optimisation."""
    st.header("Lancer l'optimisation")

    if not st.session_state.missions:
        st.warning("Aucune mission définie. Importez ou ajoutez des missions.")
        return

    if not st.session_state.auditors:
        st.warning("Aucun auditeur défini. Importez ou ajoutez des auditeurs.")
        return

    # Choix de l'algorithme
    st.subheader("Algorithme d'optimisation")
    col_algo1, col_algo2 = st.columns(2)

    with col_algo1:
        _opt_order = ["CPSAT", "Hybrid", "Genetic", "MILP"]
        optimizer_type = st.radio(
            "Choisir l'algorithme",
            ["CP-SAT (Recommandé)", "Hybride", "Génétique seul", "MILP seul (CP-SAT)"],
            index=_opt_order.index(st.session_state.optimizer_type) if st.session_state.optimizer_type in _opt_order else 0,
            help="CP-SAT (OR-Tools): solveur exact ultra-rapide. Recommandé pour tous les cas."
        )
        if "CP-SAT (Recommandé)" in optimizer_type:
            st.session_state.optimizer_type = "CPSAT"
        elif "Hybride" in optimizer_type:
            st.session_state.optimizer_type = "Hybrid"
        elif "Génétique" in optimizer_type:
            st.session_state.optimizer_type = "Genetic"
        else:
            st.session_state.optimizer_type = "MILP"

    with col_algo2:
        if st.session_state.optimizer_type == "CPSAT":
            st.success(
                "**CP-SAT (Recommandé)**\n\n"
                "- Solveur exact OR-Tools, multi-cœurs\n"
                "- Solution optimale en quelques secondes\n"
                "- Minimise la sous-traitance, équilibre la charge\n"
                "- Respect strict de toutes les contraintes"
            )
        elif st.session_state.optimizer_type == "Hybrid":
            st.info(
                "**Mode Hybride**\n\n"
                "1. Génétique: 500 générations (~5 min)\n"
                "2. CP-SAT: affinage\n"
                "- Plus lent, rarement nécessaire"
            )
        elif st.session_state.optimizer_type == "Genetic":
            st.info(
                "**Algorithme Génétique**\n\n"
                "- Solution approchée de bonne qualité\n"
                "- 300 générations (~3 min)\n"
                "- Utilise tous les coeurs CPU"
            )
        else:
            st.info(
                "**MILP seul (moteur CP-SAT)**\n\n"
                "- Solution optimale garantie\n"
                "- Identique à CP-SAT recommandé\n"
                "- Respect strict des contraintes"
            )

    # Sous-traitance disponible pour absorber les surcharges
    st.subheader("Sous-traitance (jours)")

    # Borne max du curseur = volume total des missions (on ne peut pas sous-traiter
    # plus que tout le travail). Arrondi au multiple de 50 supérieur.
    total_mission_days = int(sum(m.budget_days for m in st.session_state.get('missions', [])))
    slider_max = max(100, ((total_mission_days // 50) + 1) * 50)

    col_sub1, col_sub2 = st.columns([3, 2])

    with col_sub1:
        unlimited_sub = st.checkbox(
            "Sous-traitance illimitée (minimisée automatiquement)",
            value=st.session_state.get('unlimited_subcontracting', True),
            help="Décocher pour plafonner les jours de sous-traitance avec le curseur."
        )
        st.session_state.unlimited_subcontracting = unlimited_sub

        if not unlimited_sub:
            default_val = st.session_state.get('max_subcontracting_days') or min(200, slider_max)
            max_subcontracting_days = st.slider(
                "Jours de sous-traitance disponibles",
                min_value=0,
                max_value=slider_max,
                value=int(min(default_val, slider_max)),
                step=25,
                help="Nombre maximum de jours-homme que le solveur peut sous-traiter "
                     "pour absorber les surcharges. Le solveur en utilise le moins "
                     "possible, sans jamais dépasser ce plafond."
            )
            st.session_state.max_subcontracting_days = max_subcontracting_days
        else:
            st.session_state.max_subcontracting_days = None

    with col_sub2:
        if unlimited_sub:
            st.caption(
                "Le solveur utilise autant de sous-traitance que nécessaire pour "
                "lever toutes les surcharges, en la **minimisant**."
            )
        else:
            st.caption(
                f"Plafond : **{st.session_state.max_subcontracting_days} j** "
                f"(sur {total_mission_days} j de missions). Si ce plafond est trop "
                f"bas pour absorber la surcharge, le solveur indique le **minimum "
                f"de jours nécessaire**."
            )

    # Taux d'occupation minimal des auditeurs
    st.subheader("Taux d'occupation minimal des auditeurs")
    col_occ1, col_occ2 = st.columns(2)

    with col_occ1:
        min_occupancy = st.slider(
            "Charge minimale visée par auditeur (%)",
            min_value=0,
            max_value=100,
            value=int(st.session_state.get('min_load_ratio', 0.80) * 100),
            step=5,
            help="Le solveur cherche à charger chaque auditeur (exécutant) au moins "
                 "à ce pourcentage de sa capacité. Mettre 0 pour désactiver."
        )
        st.session_state.min_load_ratio = min_occupancy / 100.0

    with col_occ2:
        st.caption(
            "Objectif appliqué aux **exécutants** (les superviseurs/validateurs "
            "en sont exclus). Préférence forte : si le volume de travail ou les "
            "disponibilités ne permettent pas d'atteindre ce seuil, le solveur "
            "s'en approche au plus près et le signale."
        )

    # Paramètres de performance MILP
    st.subheader("Performance MILP")
    col_perf1, col_perf2 = st.columns(2)

    with col_perf1:
        gap_options = {
            "Rapide (10%)": 0.10,
            "Équilibré (5%)": 0.05,
            "Précis (2%)": 0.02,
            "Optimal (1%)": 0.01
        }
        gap_choice = st.selectbox(
            "Tolérance d'optimalité",
            list(gap_options.keys()),
            index=0,  # Rapide par défaut
            help="Plus la tolérance est élevée, plus c'est rapide mais moins optimal"
        )
        st.session_state.gap_tolerance = gap_options[gap_choice]

    with col_perf2:
        time_limit = st.number_input(
            "Temps max MILP (secondes)",
            min_value=60,
            max_value=3600,
            value=st.session_state.get('milp_time_limit', 300),
            step=60,
            help="Temps maximum pour la résolution MILP"
        )
        st.session_state.milp_time_limit = time_limit

    # Section Logs (collapsible)
    with st.expander("Logs", expanded=False):
        col_log1, col_log2 = st.columns([3, 1])
        with col_log2:
            if st.button("Effacer logs"):
                logger.clear()
                st.rerun()

        log_entries = logger.get_all_messages()
        if log_entries:
            # Afficher les 50 derniers logs
            st.code("\n".join(log_entries[-50:]), language=None)
        else:
            st.write("*Aucun log*")

    st.markdown("---")

    # Analyse préliminaire
    st.subheader("Analyse préliminaire")

    analysis = analyze_feasibility(
        st.session_state.missions,
        st.session_state.auditors
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Missions", analysis["num_missions"])
    with col2:
        st.metric("Auditeurs", analysis["num_auditors"])
    with col3:
        st.metric("Jours missions", f"{analysis['total_mission_days']:.0f}")
    with col4:
        st.metric("Jours disponibles", f"{analysis['total_auditor_days']:.0f}")

    # Couverture par compétence
    st.write("**Couverture par compétence:**")
    skill_data = []
    for skill, data in analysis["skill_coverage"].items():
        skill_data.append({
            "Compétence": skill,
            "Auditeurs": data["auditors"],
            "Jours requis": f"{data['required_days']:.0f}",
            "Status": data["status"]
        })
    st.dataframe(pd.DataFrame(skill_data), use_container_width=True)

    # Alertes (loguées)
    if analysis["bottlenecks"]:
        for b in analysis["bottlenecks"]:
            logger.warning("Analyse", b)

    if analysis["recommendations"]:
        for r in analysis["recommendations"]:
            logger.info("Analyse", f"Recommandation: {r}")

    # Validation des données
    st.markdown("---")
    st.subheader("Validation des données")

    is_valid, errors, warnings = validate_input_data(
        st.session_state.missions,
        st.session_state.auditors
    )

    if errors:
        for e in errors:
            logger.error("Validation", e)
        st.error(f"{len(errors)} erreurs (voir logs)")
    if warnings:
        for w in warnings:
            logger.warning("Validation", w)
    if not errors:
        st.success("Données valides")

    # Bouton d'optimisation
    st.markdown("---")

    if st.button("Lancer l'optimisation", type="primary", disabled=not is_valid):
        # Effacer les logs précédents
        logger.clear()
        logger.info("Optimisation", "Démarrage de l'optimisation...")

        # Barre de progression avec étapes
        progress_bar = st.progress(0)
        status_text = st.empty()
        # Zone de détails en temps réel
        details_container = st.container()
        with details_container:
            col1, col2, col3 = st.columns(3)
            detail_col1 = col1.empty()
            detail_col2 = col2.empty()
            detail_col3 = col3.empty()

        # Étape 1: Préparation
        status_text.markdown("**Étape 1/5:** Préparation des données...")
        progress_bar.progress(5)

        config = PlanningConfig(year=st.session_state.year)

        # Capturer les données AVANT de lancer le thread (session_state non accessible dans thread)
        missions_copy = list(st.session_state.missions)
        auditors_copy = list(st.session_state.auditors)

        opt_type = st.session_state.optimizer_type
        result = None

        # Récupérer la limite de sous-traitance si définie
        max_sub_days = st.session_state.get('max_subcontracting_days', None)

        # Paramètres MILP depuis session_state
        milp_gap = st.session_state.get('gap_tolerance', 0.10)  # 10% par défaut (rapide)
        milp_time = st.session_state.get('milp_time_limit', 300)  # 5 min par défaut
        # Taux d'occupation minimal choisi par l'utilisateur (défaut 80%)
        min_load = st.session_state.get('min_load_ratio', 0.80)

        # MODE CP-SAT (Recommandé): solveur exact rapide
        if opt_type == "CPSAT":
            status_text.markdown("**Étape 2/3:** Résolution CP-SAT (solveur exact)...")
            progress_bar.progress(20)

            optimizer = AuditStaffingOptimizer(config)
            algo_name = "CP-SAT"
            # CP-SAT est rapide : une limite courte suffit (l'optimum tombe en secondes)
            cpsat_time = max(30, min(milp_time, 120))

            result_container = {"result": None, "done": False, "error": None}

            def run_cpsat(missions, auditors, time_lim, gap_tol, max_sub, min_ld):
                try:
                    result_container["result"] = optimizer.optimize(
                        missions, auditors, verbose=False,
                        time_limit=time_lim,
                        gap_tolerance=gap_tol,
                        max_subcontracting_days=max_sub,
                        min_load_ratio=min_ld
                    )
                except Exception as e:
                    result_container["error"] = str(e)
                finally:
                    result_container["done"] = True

            thread = threading.Thread(
                target=run_cpsat,
                args=(missions_copy, auditors_copy, cpsat_time, milp_gap, max_sub_days, min_load)
            )
            thread.start()

            start_time = time.time()
            while not result_container["done"]:
                elapsed = time.time() - start_time
                progress = 20 + min(70, int(elapsed / cpsat_time * 70))
                progress_bar.progress(progress)
                mins, secs = int(elapsed // 60), int(elapsed % 60)
                status_text.markdown(f"**Étape 2/3:** CP-SAT en cours... ({mins}:{secs:02d})")

                info = optimizer.progress_info
                detail_col1.metric("Solveur", info.get('solver', 'CP-SAT'))
                detail_col2.metric("Variables", f"{info.get('num_variables', 0):,}")
                detail_col3.metric("Contraintes", f"{info.get('num_constraints', 0):,}")

                time.sleep(0.2)

            thread.join()
            result = result_container["result"]
            if result_container["error"]:
                logger.error("CP-SAT", f"Erreur: {result_container['error']}")

        # MODE HYBRIDE: Génétique d'abord, puis MILP pour affiner
        elif opt_type == "Hybrid":
            # Phase 1: Algorithme Génétique (plus de générations pour problème complexe)
            status_text.markdown("**Étape 2/5:** Optimisation Génétique (solution initiale)...")
            progress_bar.progress(15)

            genetic_optimizer = GeneticAuditOptimizer(
                config,
                population_size=300,
                generations=500,
                mutation_rate=0.15,
                crossover_rate=0.85,
                elite_size=30,
                max_subcontracting_days=max_sub_days
            )

            result_container = {"result": None, "done": False}

            def run_genetic(missions, auditors):
                result_container["result"] = genetic_optimizer.optimize(missions, auditors, verbose=False)
                result_container["done"] = True

            thread = threading.Thread(target=run_genetic, args=(missions_copy, auditors_copy))
            thread.start()

            start_time = time.time()
            while not result_container["done"]:
                elapsed = time.time() - start_time
                progress = 15 + min(25, int(elapsed / 60 * 25))
                progress_bar.progress(progress)
                mins, secs = int(elapsed // 60), int(elapsed % 60)
                status_text.markdown(f"**Étape 2/5:** Optimisation Génétique... ({mins}:{secs:02d})")

                # Afficher détails génétique
                info = genetic_optimizer.progress_info
                detail_col1.metric("Génération", f"{info.get('generation', 0)}/{info.get('total_generations', 500)}")
                detail_col2.metric("Meilleur Fitness", f"{info.get('best_fitness', 0):.1f}")
                detail_col3.metric("Conflits", f"{info.get('best_conflicts', '?')}")

                time.sleep(0.3)

            thread.join()
            genetic_result = result_container["result"]

            # Phase 2: MILP pour affiner et garantir le respect du budget
            status_text.markdown("**Étape 3/5:** Démarrage MILP (construction du modèle)...")
            progress_bar.progress(45)
            time.sleep(0.5)  # Laisser le temps à l'utilisateur de voir

            milp_optimizer = AuditStaffingOptimizer(config)

            result_container = {"result": None, "done": False, "error": None}

            def run_milp(missions, auditors, time_lim, gap_tol, max_sub, min_ld):
                try:
                    result_container["result"] = milp_optimizer.optimize(
                        missions, auditors, verbose=False,
                        time_limit=time_lim,
                        gap_tolerance=gap_tol,
                        max_subcontracting_days=max_sub,
                        min_load_ratio=min_ld
                    )
                except Exception as e:
                    result_container["error"] = str(e)
                finally:
                    result_container["done"] = True

            thread = threading.Thread(target=run_milp, args=(missions_copy, auditors_copy, milp_time, milp_gap, max_sub_days, min_load))
            thread.start()

            start_time = time.time()
            max_milp_time = milp_time
            min_display_time = 3  # Afficher MILP au moins 3 secondes

            while not result_container["done"] and (time.time() - start_time) < max_milp_time:
                elapsed = time.time() - start_time
                progress = 45 + min(40, int(elapsed / max_milp_time * 40))
                progress_bar.progress(progress)
                mins, secs = int(elapsed // 60), int(elapsed % 60)

                # Afficher détails MILP
                info = milp_optimizer.progress_info
                solver_name = info.get('solver', 'Construction...')
                num_vars = info.get('num_variables', 0)
                num_constraints = info.get('num_constraints', 0)

                status_text.markdown(f"**Étape 3/5:** MILP {solver_name}... ({mins}:{secs:02d})")
                detail_col1.metric("Solveur", solver_name)
                detail_col2.metric("Variables", f"{num_vars:,}")
                detail_col3.metric("Contraintes", f"{num_constraints:,}")

                time.sleep(0.3)

            # Attendre au moins min_display_time pour que l'utilisateur voie l'étape
            elapsed = time.time() - start_time
            if elapsed < min_display_time:
                remaining = min_display_time - elapsed
                status_text.markdown(f"**Étape 3/5:** MILP terminé, finalisation...")
                time.sleep(remaining)

            thread.join()
            milp_result = result_container["result"]

            # Log erreur si présente
            if result_container["error"]:
                logger.error("Hybride", f"Erreur MILP: {result_container['error']}")

            # Choisir le meilleur résultat
            status_text.markdown("**Étape 4/5:** Comparaison des résultats...")
            progress_bar.progress(90)

            # Calculer les métriques de chaque solution
            genetic_ok = genetic_result and genetic_result.success
            milp_ok = milp_result and milp_result.success

            if milp_ok and genetic_ok:
                # Les deux ont réussi - comparer la sous-traitance
                genetic_ext = sum(a.days for a in genetic_result.external_allocations)
                milp_ext = sum(a.days for a in milp_result.external_allocations)

                # Vérifier le respect de la limite sous-traitance (CONTRAINTE DURE)
                genetic_valid = True
                milp_valid = True
                if max_sub_days is not None:
                    genetic_valid = genetic_ext <= max_sub_days
                    milp_valid = milp_ext <= max_sub_days

                if milp_valid and not genetic_valid:
                    # Seul MILP respecte la limite
                    result = milp_result
                    logger.info("Hybride", f"MILP retenu (respecte limite {max_sub_days}j, génétique = {genetic_ext:.0f}j)")
                elif genetic_valid and not milp_valid:
                    # Seul Génétique respecte la limite
                    result = genetic_result
                    logger.info("Hybride", f"Génétique retenu (respecte limite {max_sub_days}j, MILP = {milp_ext:.0f}j)")
                elif milp_ext <= genetic_ext:
                    result = milp_result
                    logger.info("Hybride", f"MILP retenu ({milp_ext:.0f}j sous-traitance vs {genetic_ext:.0f}j génétique)")
                else:
                    result = genetic_result
                    logger.info("Hybride", f"Génétique retenu ({genetic_ext:.0f}j sous-traitance vs {milp_ext:.0f}j MILP)")
            elif milp_ok:
                result = milp_result
                logger.info("Hybride", "Solution MILP (génétique n'a pas convergé)")
            elif genetic_ok:
                result = genetic_result
                logger.info("Hybride", "Solution génétique (MILP n'a pas convergé)")
            else:
                result = genetic_result if genetic_result else milp_result
                if result:
                    logger.warning("Hybride", "Aucune solution optimale trouvée")

            algo_name = "Hybride"

        # MODE GÉNÉTIQUE SEUL
        elif opt_type == "Genetic":
            status_text.markdown("**Étape 2/4:** Construction du modèle génétique...")
            progress_bar.progress(15)

            optimizer = GeneticAuditOptimizer(
                config,
                population_size=300,
                generations=500,
                mutation_rate=0.15,
                crossover_rate=0.85,
                elite_size=30,
                max_subcontracting_days=max_sub_days
            )
            algo_name = "Génétique"
            max_time = 300  # 5 minutes

            result_container = {"result": None, "done": False}

            def run_optimization(missions, auditors):
                result_container["result"] = optimizer.optimize(missions, auditors, verbose=False)
                result_container["done"] = True

            thread = threading.Thread(target=run_optimization, args=(missions_copy, auditors_copy))
            thread.start()

            status_text.markdown(f"**Étape 3/4:** Résolution {algo_name} en cours...")

            start_time = time.time()
            while not result_container["done"]:
                elapsed = time.time() - start_time
                progress = 20 + min(65, int(elapsed / max_time * 65))
                progress_bar.progress(progress)
                mins, secs = int(elapsed // 60), int(elapsed % 60)
                status_text.markdown(f"**Étape 3/4:** Résolution {algo_name}... ({mins}:{secs:02d})")

                # Afficher détails génétique
                info = optimizer.progress_info
                detail_col1.metric("Génération", f"{info.get('generation', 0)}/{info.get('total_generations', 300)}")
                detail_col2.metric("Meilleur Fitness", f"{info.get('best_fitness', 0):.1f}")
                detail_col3.metric("Conflits", f"{info.get('best_conflicts', '?')}")

                time.sleep(0.3)

            thread.join()
            result = result_container["result"]

        # MODE MILP SEUL
        else:
            status_text.markdown("**Étape 2/4:** Construction du modèle MILP...")
            progress_bar.progress(15)

            optimizer = AuditStaffingOptimizer(config)
            algo_name = "MILP"
            max_time = milp_time

            result_container = {"result": None, "done": False}

            def run_optimization(missions, auditors, time_lim, gap_tol, max_sub, min_ld):
                result_container["result"] = optimizer.optimize(
                    missions, auditors, verbose=False,
                    time_limit=time_lim,
                    gap_tolerance=gap_tol,
                    max_subcontracting_days=max_sub,
                    min_load_ratio=min_ld
                )
                result_container["done"] = True

            thread = threading.Thread(target=run_optimization, args=(missions_copy, auditors_copy, milp_time, milp_gap, max_sub_days, min_load))
            thread.start()

            status_text.markdown(f"**Étape 3/4:** Résolution {algo_name} en cours (max {milp_time//60} min)...")

            # Animation de progression pendant la résolution MILP
            start_time = time.time()
            while not result_container["done"]:
                elapsed = time.time() - start_time
                progress = 20 + min(65, int(elapsed / max_time * 65))
                progress_bar.progress(progress)
                mins, secs = int(elapsed // 60), int(elapsed % 60)
                status_text.markdown(f"**Étape 3/4:** Résolution {algo_name}... ({mins}:{secs:02d})")

                # Afficher détails MILP
                info = optimizer.progress_info
                detail_col1.metric("Solveur", info.get('solver', 'En attente...'))
                detail_col2.metric("Variables", f"{info.get('num_variables', 0):,}")
                detail_col3.metric("Contraintes", f"{info.get('num_constraints', 0):,}")

                time.sleep(0.3)

            thread.join()
            result = result_container["result"]

        # Finalisation commune
        status_text.markdown("**Finalisation:** Extraction des résultats...")
        progress_bar.progress(95)

        st.session_state.result = result

        # Terminé
        progress_bar.progress(100)
        status_text.empty()

        if result and result.success:
            st.success(f"Optimisation {algo_name} terminée avec succès!")

            # Vérifier si la limite de sous-traitance est respectée
            if result.external_allocations and max_sub_days is not None:
                ext_days = sum(a.days for a in result.external_allocations)
                if ext_days > max_sub_days:
                    st.error(f"⚠️ LIMITE SOUS-TRAITANCE DÉPASSÉE: {ext_days:.0f}j utilisés vs {max_sub_days}j max")
                    logger.error("Validation", f"Limite sous-traitance dépassée: {ext_days:.0f}j > {max_sub_days}j")
                else:
                    st.info(f"Sous-traitance: {ext_days:.0f}j / {max_sub_days}j max")
            elif result.external_resources_needed:
                st.info("Des ressources externes ont été nécessaires.")

            # Sauvegarde automatique du planning
            if save_planning_auto(result, missions_copy, auditors_copy, st.session_state.year):
                st.info("💾 Planning sauvegardé automatiquement")
        else:
            st.error(f"L'optimisation {algo_name} n'a pas trouvé de solution.")

        # Afficher les messages
        if result:
            for msg in result.messages:
                st.write(f"- {msg}")

        # Afficher les logs de l'optimisation
        st.subheader("Journal d'optimisation")
        log_entries = logger.get_all_messages()
        if log_entries:
            for entry in log_entries:
                # Colorer selon le niveau
                if "[ERROR]" in entry:
                    st.error(entry)
                elif "[WARNING]" in entry:
                    st.warning(entry)
                else:
                    st.info(entry)


def render_results_tab():
    """Onglet des résultats."""
    st.header("Résultats de l'optimisation")

    result = st.session_state.result
    if not result or not result.success:
        st.info("Aucun résultat disponible. Lancez d'abord l'optimisation.")
        return

    all_allocations = result.allocations + result.external_allocations

    # Vérifications de qualité
    conflicts = result.check_conflicts()
    auditor_issues = result.check_auditor_count(st.session_state.missions)
    gap_issues = result.check_gaps()
    team_issues = result.check_team_consistency()
    partial_issues = result.check_partial_weeks()
    budget_issues = result.check_budget(st.session_state.missions)

    # Logger les problèmes
    for c in conflicts:
        logger.error("Validation", f"Conflit: {c}")
    for issue in auditor_issues:
        logger.warning("Validation", f"Nb auditeurs: {issue}")
    for issue in gap_issues:
        logger.warning("Validation", f"Trou mission: {issue}")
    for issue in team_issues:
        logger.warning("Validation", f"Équipe: {issue}")
    for issue in partial_issues:
        logger.warning("Validation", f"Semaine partielle: {issue}")
    for issue in budget_issues:
        logger.error("Validation", f"Budget: {issue}")

    # Afficher les résultats de validation
    if not conflicts:
        st.success("Aucun conflit: chaque auditeur sur une seule mission par semaine")
    else:
        st.error(f"{len(conflicts)} conflits détectés!")
        with st.expander("Voir les conflits"):
            for c in conflicts:
                st.write(f"- {c}")

    if auditor_issues:
        st.warning(f"{len(auditor_issues)} problèmes de nombre d'auditeurs")
        with st.expander("Voir les problèmes d'auditeurs"):
            for issue in auditor_issues:
                st.write(f"- {issue}")

    if gap_issues:
        st.warning(f"{len(gap_issues)} trous dans les missions")
        with st.expander("Voir les trous"):
            for issue in gap_issues:
                st.write(f"- {issue}")

    if team_issues:
        st.warning(f"{len(team_issues)} problèmes d'équipe")
        with st.expander("Voir les problèmes d'équipe"):
            for issue in team_issues:
                st.write(f"- {issue}")

    if partial_issues:
        st.warning(f"{len(partial_issues)} semaines partielles")
        with st.expander("Voir les semaines partielles"):
            for issue in partial_issues:
                st.write(f"- {issue}")

    # Vérification du budget par mission (IMPORTANT)
    if budget_issues:
        st.error(f"{len(budget_issues)} problèmes de budget!")
        with st.expander("Voir les problèmes de budget", expanded=True):
            for issue in budget_issues:
                st.write(f"- {issue}")
    else:
        st.success("Budgets respectés")

    # Vérification de la limite de sous-traitance
    max_sub_days = st.session_state.get('max_subcontracting_days', None)
    if result.external_allocations:
        ext_days = sum(a.days for a in result.external_allocations)
        if max_sub_days is not None:
            if ext_days > max_sub_days:
                st.error(f"⚠️ LIMITE SOUS-TRAITANCE DÉPASSÉE: {ext_days:.0f}j > {max_sub_days}j max")
            else:
                st.success(f"Sous-traitance respectée: {ext_days:.0f}j / {max_sub_days}j max")
        else:
            st.info(f"Sous-traitance utilisée: {ext_days:.0f}j (pas de limite définie)")

    # Noms des superviseurs (validateurs) pour séparer les deux plannings
    supervisor_names = {a.name for a in st.session_state.auditors if a.is_supervisor}

    # ===== PLANNING DES AUDITEURS (exécutants) =====
    st.subheader("Planning des auditeurs — par mission (semaines)")
    st.caption("Équipes d'exécution. Le chef de mission est marqué « (S) ». "
               "Les superviseurs/validateurs ont leur propre planning ci-dessous.")

    planning_matrix = result.get_planning_matrix()

    # Construire un DataFrame large: lignes = missions, colonnes = S1..S52
    data = []
    for mission in st.session_state.missions:
        row = {"Mission": mission.name}
        for w in range(1, 53):
            if mission.name in planning_matrix and w in planning_matrix[mission.name]:
                row[f"S{w}"] = "\n".join(planning_matrix[mission.name][w])
            else:
                row[f"S{w}"] = ""
        data.append(row)

    df_planning = pd.DataFrame(data)
    st.dataframe(df_planning, use_container_width=True, height=400)

    # ===== PLANNING DES SUPERVISEURS (validateurs) — SÉPARÉ =====
    st.markdown("---")
    st.subheader("Planning des superviseurs (validateurs)")
    st.caption("Missions validées par chaque superviseur, par semaine. Un superviseur "
               "peut valider plusieurs missions en parallèle. « (P) » = il participe "
               "aussi à la mission comme ressource.")

    sup_planning = build_supervisor_planning(st.session_state.missions, result)
    if sup_planning:
        sup_data = []
        for sup in sorted(sup_planning.keys()):
            row = {"Superviseur": sup}
            for w in range(1, 53):
                labels = sup_planning[sup].get(w, [])
                row[f"S{w}"] = "\n".join(labels)
            sup_data.append(row)
        st.dataframe(pd.DataFrame(sup_data), use_container_width=True, height=200)

        # Charge de supervision
        sup_stats = calculate_supervisor_stats(st.session_state.missions, result)
        st.markdown("**Charge de supervision**")
        st.dataframe(pd.DataFrame([
            {
                "Superviseur": sup,
                "Missions validées": s["missions"],
                "Semaines de supervision": s["weeks"],
                "Pic missions simultanées": s["max_concurrent"],
                "Participations": s["participations"],
            }
            for sup, s in sorted(sup_stats.items())
        ]), use_container_width=True)
    else:
        st.info("Aucun superviseur désigné sur les missions.")

    # ===== STATISTIQUES PAR AUDITEUR (exécutants uniquement) =====
    st.markdown("---")
    st.subheader("Utilisation des auditeurs (exécutants)")

    stats = calculate_utilization_stats(st.session_state.auditors, all_allocations)

    # Seuil de charge minimal choisi par l'utilisateur
    MIN_LOAD_PERCENT = int(st.session_state.get('min_load_ratio', 0.80) * 100)
    underloaded_auditors = []

    auditor_stats = []
    for name, data in stats.items():
        # Exclure les superviseurs (validateurs) de ce tableau -> planning séparé
        if name in supervisor_names:
            continue
        util = data["utilization"]
        is_internal = not name.startswith("Sous-traitant_")

        if is_internal and util < MIN_LOAD_PERCENT:
            underloaded_auditors.append((name, util))

        auditor_stats.append({
            "Auditeur": name,
            "Budget (j)": data["budget"],
            "Alloué (j)": data["allocated"],
            "Utilisation (%)": util,
            "Statut": "⚠️ Sous-chargé" if (is_internal and util < MIN_LOAD_PERCENT) else "✅ OK",
            "Missions": ", ".join(data["missions"]) if data["missions"] else "-"
        })

    # Afficher l'alerte si des auditeurs sont sous-chargés
    if underloaded_auditors:
        st.warning(f"⚠️ {len(underloaded_auditors)} auditeur(s) sous-chargé(s) (< {MIN_LOAD_PERCENT}%)")
        for name, util in underloaded_auditors:
            st.write(f"  - {name}: {util:.0f}%")
    else:
        st.success(f"✅ Tous les auditeurs exécutants sont chargés à {MIN_LOAD_PERCENT}% ou plus")

    st.dataframe(pd.DataFrame(auditor_stats), use_container_width=True)

    # Téléchargement des résultats
    st.markdown("---")
    st.subheader("Télécharger les résultats")

    if st.button("Générer le fichier Excel"):
        writer = ExcelWriter()

        # Tableau de bord (première feuille)
        writer.create_dashboard_sheet(
            result,
            st.session_state.missions,
            st.session_state.auditors
        )
        # Planning détaillé (par mission)
        writer.create_planning_sheet(result, st.session_state.missions)
        # Planning des auditeurs exécutants (superviseurs exclus)
        writer.create_auditor_planning_sheet(result, st.session_state.auditors)
        # Planning des superviseurs (validateurs) — feuille SÉPARÉE
        writer.create_supervisor_planning_sheet(
            result, st.session_state.missions, st.session_state.auditors
        )
        # Résumé
        writer.create_summary_sheet(
            result,
            st.session_state.missions,
            st.session_state.auditors
        )
        # Légende
        writer.create_legend_sheet(st.session_state.auditors)

        excel_bytes = writer.get_bytes()

        st.download_button(
            label="📥 Télécharger Planning Excel",
            data=excel_bytes,
            file_name=f"planning_audit_{st.session_state.year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


def create_template_excel_buffer(buffer: BytesIO) -> None:
    """Crée le template Excel dans un buffer."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # Feuille Missions
    ws_missions = wb.active
    ws_missions.title = "Missions"

    mission_headers = [
        "Nom Mission", "Type Période", "Période", "Budget (jours)",
        "Superviseur", "Nb Auditeurs", "Compétences Requises"
    ]
    for col, header in enumerate(mission_headers, start=1):
        cell = ws_missions.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        ws_missions.column_dimensions[get_column_letter(col)].width = 20

    # Feuille Auditeurs
    ws_auditors = wb.create_sheet("Auditeurs")

    auditor_headers = [
        "Nom", "Budget (jours)", "Compétences", "Indisponibilités", "Peut Superviser"
    ]
    for col, header in enumerate(auditor_headers, start=1):
        cell = ws_auditors.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        ws_auditors.column_dimensions[get_column_letter(col)].width = 20

    wb.save(buffer)
    buffer.seek(0)


def render_auditors_tab():
    """Onglet de gestion complète des auditeurs."""
    st.header("Gestion des Auditeurs")

    # Auto-détection semaine/année courante
    current_week = datetime.now().isocalendar()[1]
    current_year = datetime.now().year

    # Statistiques en haut
    stats = auditor_manager.get_statistics(current_year)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total auditeurs", stats['total'])
    with col2:
        st.metric("Actifs", stats['actifs'], delta=None)
    with col3:
        st.metric("Futurs arrivants", stats['futurs'])
    with col4:
        st.metric("Partis", stats['partis'])

    st.markdown("---")

    # Deux colonnes principales
    col_list, col_detail = st.columns([1, 2])

    with col_list:
        st.subheader("Liste des auditeurs")

        # Filtres
        filter_status = st.selectbox(
            "Filtrer par statut",
            ["Tous", "Actif", "Futur", "Parti"],
            key="filter_status"
        )

        filter_grade = st.selectbox(
            "Filtrer par grade",
            ["Tous", "Manager", "Senior", "Confirmé", "Junior"],
            key="filter_grade"
        )

        # Liste filtrée
        all_profiles = auditor_manager.get_all()

        # Mettre à jour les statuts
        for p in all_profiles:
            p._update_status(current_week, current_year)

        filtered = all_profiles
        if filter_status != "Tous":
            filtered = [p for p in filtered if p.status.value == filter_status]
        if filter_grade != "Tous":
            filtered = [p for p in filtered if p.grade.value == filter_grade]

        # Afficher la liste
        if not filtered:
            st.info("Aucun auditeur trouvé")
        else:
            for profile in filtered:
                # Icône de statut
                status_icon = "🟢" if profile.status == AuditorStatus.ACTIF else (
                    "🟡" if profile.status == AuditorStatus.FUTUR else "🔴"
                )

                col_name, col_btn = st.columns([3, 1])
                with col_name:
                    st.write(f"{status_icon} **{profile.name}** ({profile.grade.value})")
                with col_btn:
                    if st.button("Voir", key=f"view_{profile.name}"):
                        st.session_state.selected_auditor = profile.name
                        st.rerun()

        # Bouton ajouter nouvel auditeur
        st.markdown("---")
        if st.button("➕ Nouvel auditeur", type="primary"):
            st.session_state.selected_auditor = "__new__"
            st.rerun()

        # Import depuis anciens auditeurs
        if st.session_state.get('auditors') and len(st.session_state.auditors) > 0:
            st.markdown("---")
            if st.button("📥 Importer auditeurs existants"):
                imported = auditor_manager.import_from_legacy(st.session_state.auditors)
                if imported > 0:
                    st.success(f"{imported} auditeurs importés")
                    st.rerun()
                else:
                    st.info("Aucun nouvel auditeur à importer")

    with col_detail:
        selected_name = st.session_state.get('selected_auditor')

        if selected_name == "__new__":
            # Formulaire de création
            st.subheader("Nouvel auditeur")
            render_auditor_form(None)

        elif selected_name:
            profile = auditor_manager.get_by_name(selected_name)
            if profile:
                st.subheader(f"Fiche: {profile.name}")
                render_auditor_detail(profile, current_year)
            else:
                st.info("Sélectionnez un auditeur dans la liste")
        else:
            st.info("Sélectionnez un auditeur dans la liste ou créez-en un nouveau")

    # Synchronisation avec l'optimiseur
    st.markdown("---")
    st.subheader("Synchronisation avec l'optimiseur")

    col_sync1, col_sync2 = st.columns(2)
    with col_sync1:
        if st.button("🔄 Charger les auditeurs actifs pour l'optimisation"):
            year = st.session_state.get('year', current_year)
            auditors = auditor_manager.to_auditors(year)
            if auditors:
                st.session_state.auditors = auditors
                st.success(f"{len(auditors)} auditeurs actifs chargés pour l'année {year}")
            else:
                st.warning("Aucun auditeur actif trouvé")

    with col_sync2:
        st.write(f"**Auditeurs dans l'optimiseur:** {len(st.session_state.get('auditors', []))}")


def render_auditor_form(profile: Optional[AuditorProfile]):
    """Formulaire de création/édition d'un auditeur."""
    is_new = profile is None

    with st.form("auditor_form"):
        # Informations de base
        name = st.text_input(
            "Nom",
            value="" if is_new else profile.name,
            disabled=not is_new
        )

        col1, col2 = st.columns(2)
        with col1:
            grade_options = ["Junior", "Confirmé", "Senior", "Manager"]
            grade_idx = 1  # Confirmé par défaut
            if not is_new:
                grade_idx = grade_options.index(profile.grade.value)
            grade = st.selectbox("Grade", grade_options, index=grade_idx)

        with col2:
            budget = st.number_input(
                "Budget annuel (jours)",
                min_value=1,
                max_value=365,
                value=205 if is_new else int(profile.budget_days)
            )

        # Compétences
        skills_options = ["IT", "Modèle", "Assurance", "Généraliste"]
        default_skills = ["Généraliste"]
        if not is_new:
            default_skills = [s.value for s in profile.skills]
        skills = st.multiselect("Compétences", skills_options, default=default_skills)

        # Certifications
        certifications = st.text_input(
            "Certifications (séparées par virgule)",
            value="" if is_new else ", ".join(profile.certifications)
        )

        # Superviseur
        is_supervisor = st.checkbox(
            "Peut superviser des missions",
            value=False if is_new else profile.is_supervisor
        )

        # Dates
        st.markdown("**Dates de présence**")
        col_arr, col_dep = st.columns(2)

        with col_arr:
            has_arrival = st.checkbox(
                "Nouvel arrivant",
                value=False if is_new else (profile.arrival_week is not None)
            )
            if has_arrival:
                arr_col1, arr_col2 = st.columns(2)
                with arr_col1:
                    arrival_week = st.number_input(
                        "Semaine arrivée",
                        min_value=1,
                        max_value=52,
                        value=1 if is_new else (profile.arrival_week or 1)
                    )
                with arr_col2:
                    arrival_year = st.number_input(
                        "Année arrivée",
                        min_value=2020,
                        max_value=2030,
                        value=datetime.now().year if is_new else (profile.arrival_year or datetime.now().year)
                    )
            else:
                arrival_week = None
                arrival_year = None

        with col_dep:
            has_departure = st.checkbox(
                "Départ prévu",
                value=False if is_new else (profile.departure_week is not None)
            )
            if has_departure:
                dep_col1, dep_col2 = st.columns(2)
                with dep_col1:
                    departure_week = st.number_input(
                        "Semaine départ",
                        min_value=1,
                        max_value=52,
                        value=52 if is_new else (profile.departure_week or 52)
                    )
                with dep_col2:
                    departure_year = st.number_input(
                        "Année départ",
                        min_value=2020,
                        max_value=2030,
                        value=datetime.now().year if is_new else (profile.departure_year or datetime.now().year)
                    )
            else:
                departure_week = None
                departure_year = None

        # Boutons
        col_save, col_cancel = st.columns(2)
        with col_save:
            submitted = st.form_submit_button("💾 Sauvegarder", type="primary")
        with col_cancel:
            cancelled = st.form_submit_button("❌ Annuler")

        if submitted:
            if not name.strip():
                st.error("Le nom est obligatoire")
            else:
                # Construire les compétences
                skill_set = set()
                for s in skills:
                    skill = Skill.from_string(s)
                    if skill:
                        skill_set.add(skill)
                if not skill_set:
                    skill_set = {Skill.GENERALISTE}

                # Construire les certifications
                cert_list = [c.strip() for c in certifications.split(",") if c.strip()]

                new_profile = AuditorProfile(
                    name=name.strip(),
                    grade=AuditorGrade.from_string(grade) or AuditorGrade.CONFIRME,
                    budget_days=budget,
                    skills=skill_set,
                    certifications=cert_list,
                    arrival_week=arrival_week if has_arrival else None,
                    arrival_year=arrival_year if has_arrival else None,
                    departure_week=departure_week if has_departure else None,
                    departure_year=departure_year if has_departure else None,
                    is_supervisor=is_supervisor,
                    unavailabilities=[] if is_new else profile.unavailabilities
                )

                if is_new:
                    if auditor_manager.add(new_profile):
                        st.success(f"Auditeur '{name}' créé")
                        st.session_state.selected_auditor = name
                        st.rerun()
                    else:
                        st.error("Erreur: cet auditeur existe déjà")
                else:
                    if auditor_manager.update(profile.name, new_profile):
                        st.success(f"Auditeur '{name}' mis à jour")
                        st.rerun()
                    else:
                        st.error("Erreur lors de la mise à jour")

        if cancelled:
            st.session_state.selected_auditor = None
            st.rerun()


def render_auditor_detail(profile: AuditorProfile, current_year: int):
    """Affiche le détail d'un auditeur avec ses indisponibilités."""

    # Informations générales
    col1, col2 = st.columns(2)

    with col1:
        st.write(f"**Grade:** {profile.grade.value}")
        st.write(f"**Budget:** {profile.budget_days} jours/an")
        st.write(f"**Compétences:** {', '.join(s.value for s in profile.skills)}")
        if profile.certifications:
            st.write(f"**Certifications:** {', '.join(profile.certifications)}")
        st.write(f"**Superviseur:** {'Oui' if profile.is_supervisor else 'Non'}")

    with col2:
        # Statut
        status_color = "🟢" if profile.status == AuditorStatus.ACTIF else (
            "🟡" if profile.status == AuditorStatus.FUTUR else "🔴"
        )
        st.write(f"**Statut:** {status_color} {profile.status.value}")

        if profile.arrival_week:
            st.write(f"**Arrivée:** S{profile.arrival_week}/{profile.arrival_year}")
        if profile.departure_week:
            st.write(f"**Départ:** S{profile.departure_week}/{profile.departure_year}")

        # Jours indisponibles
        unavail_weeks = profile.get_unavailable_weeks(current_year)
        st.write(f"**Semaines indispo ({current_year}):** {len(unavail_weeks)}")

    # Boutons d'action
    col_edit, col_del = st.columns(2)
    with col_edit:
        if st.button("✏️ Modifier"):
            st.session_state.editing_profile = True
            st.rerun()
    with col_del:
        if st.button("🗑️ Supprimer", type="secondary"):
            if auditor_manager.delete(profile.name):
                st.success(f"Auditeur '{profile.name}' supprimé")
                st.session_state.selected_auditor = None
                st.rerun()

    # Si en mode édition
    if st.session_state.get('editing_profile'):
        st.markdown("---")
        render_auditor_form(profile)
        if st.button("Annuler édition"):
            st.session_state.editing_profile = False
            st.rerun()
        return

    st.markdown("---")

    # ========================================
    # SECTION: Charge de travail et planning
    # ========================================
    result = st.session_state.get('result')
    if result and result.success:
        st.subheader("📊 Charge de travail")

        # Récupérer les allocations de cet auditeur
        all_allocations = result.allocations + result.external_allocations
        auditor_allocations = [a for a in all_allocations if a.auditor_name == profile.name]

        if auditor_allocations:
            # Calculer les statistiques
            total_days = sum(a.days for a in auditor_allocations)
            missions = set(a.mission_name for a in auditor_allocations)
            weeks_worked = set(a.week for a in auditor_allocations)

            # Afficher les métriques
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Jours alloués", f"{total_days:.0f}j")
            with col2:
                utilization = (total_days / profile.budget_days * 100) if profile.budget_days > 0 else 0
                st.metric("Utilisation", f"{utilization:.0f}%")
            with col3:
                st.metric("Missions", len(missions))
            with col4:
                st.metric("Semaines", len(weeks_worked))

            # Liste des missions
            st.markdown("**Missions assignées:**")
            mission_summary = {}
            for alloc in auditor_allocations:
                if alloc.mission_name not in mission_summary:
                    mission_summary[alloc.mission_name] = {'days': 0, 'weeks': [], 'supervisor': False}
                mission_summary[alloc.mission_name]['days'] += alloc.days
                mission_summary[alloc.mission_name]['weeks'].append(alloc.week)
                if alloc.is_supervisor:
                    mission_summary[alloc.mission_name]['supervisor'] = True

            for mission_name, data in sorted(mission_summary.items()):
                weeks_sorted = sorted(data['weeks'])
                if len(weeks_sorted) > 1:
                    weeks_str = f"S{weeks_sorted[0]}-S{weeks_sorted[-1]}"
                else:
                    weeks_str = f"S{weeks_sorted[0]}"
                sup_str = " (Superviseur)" if data['supervisor'] else ""
                st.write(f"- **{mission_name}**: {data['days']:.0f}j - {weeks_str}{sup_str}")

            # Planning visuel par semaine
            st.markdown("**Planning par semaine:**")

            # Créer un dictionnaire semaine -> mission
            week_missions = {}
            for alloc in auditor_allocations:
                if alloc.week not in week_missions:
                    week_missions[alloc.week] = []
                week_missions[alloc.week].append(alloc.mission_name)

            # Afficher par mois
            months = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun", "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]
            month_weeks = [
                (1, 4), (5, 8), (9, 13), (14, 17), (18, 22), (23, 26),
                (27, 30), (31, 35), (36, 39), (40, 44), (45, 48), (49, 52)
            ]

            unavail_weeks = profile.get_unavailable_weeks(current_year)

            # Afficher en 3 colonnes (4 mois par colonne)
            cols = st.columns(3)
            for i, (month, (start_w, end_w)) in enumerate(zip(months, month_weeks)):
                col_idx = i // 4
                with cols[col_idx]:
                    # Construire la ligne pour ce mois
                    week_info = []
                    for w in range(start_w, end_w + 1):
                        if w in unavail_weeks:
                            week_info.append("🔴")  # Indisponible
                        elif w in week_missions:
                            week_info.append("🟦")  # Travail
                        else:
                            week_info.append("⬜")  # Libre

                    st.write(f"**{month}**: {''.join(week_info)}")

            # Légende
            st.caption("🟦 Mission | 🔴 Indispo | ⬜ Libre")

        else:
            st.info("Aucune mission assignée dans le planning actuel")

        st.markdown("---")

    # Gestion des indisponibilités
    st.subheader("📅 Indisponibilités")

    # Liste des indisponibilités existantes
    if profile.unavailabilities:
        for i, unavail in enumerate(profile.unavailabilities):
            if unavail.year == current_year:
                type_icon = {
                    UnavailabilityType.CONGE: "🏖️",
                    UnavailabilityType.MALADIE: "🤒",
                    UnavailabilityType.MATERNITE: "👶",
                    UnavailabilityType.PATERNITE: "👶",
                    UnavailabilityType.FORMATION: "📚",
                    UnavailabilityType.AUTRE: "📅"
                }.get(unavail.type, "📅")

                col_info, col_del = st.columns([4, 1])
                with col_info:
                    reason_str = f" - {unavail.reason}" if unavail.reason else ""
                    st.write(f"{type_icon} **{unavail.type.value}** S{unavail.start_week}-S{unavail.end_week}{reason_str}")
                with col_del:
                    if st.button("❌", key=f"del_unavail_{i}"):
                        auditor_manager.remove_unavailability(profile.name, i)
                        st.rerun()
    else:
        st.info("Aucune indisponibilité enregistrée")

    # Ajouter une nouvelle indisponibilité
    st.markdown("**Ajouter une indisponibilité**")

    with st.form("add_unavailability"):
        col1, col2, col3 = st.columns(3)

        with col1:
            unavail_type = st.selectbox(
                "Type",
                ["Congés", "Maladie", "Maternité", "Paternité", "Formation", "Autre"]
            )

        with col2:
            start_week = st.number_input("Semaine début", min_value=1, max_value=52, value=1)

        with col3:
            end_week = st.number_input("Semaine fin", min_value=1, max_value=52, value=1)

        reason = st.text_input("Motif (optionnel)")

        year_unavail = st.number_input("Année", min_value=2020, max_value=2030, value=current_year)

        if st.form_submit_button("➕ Ajouter"):
            if start_week <= end_week:
                unavail = Unavailability(
                    type=UnavailabilityType.from_string(unavail_type) or UnavailabilityType.AUTRE,
                    start_week=start_week,
                    end_week=end_week,
                    year=year_unavail,
                    reason=reason
                )
                auditor_manager.add_unavailability(profile.name, unavail)
                st.success(f"Indisponibilité ajoutée: S{start_week}-S{end_week}")
                st.rerun()
            else:
                st.error("La semaine de fin doit être >= semaine de début")


def render_replanification_tab():
    """Onglet de replanification avec import/export."""
    st.header("Replanification")

    # Auto-détection semaine/année
    auto_week = datetime.now().isocalendar()[1]
    auto_year = datetime.now().year

    # Section 1: Planning sauvegardé automatiquement
    st.subheader("1. Planning sauvegardé")

    planning_info = get_planning_info()
    result = st.session_state.get('result')

    col_info, col_actions = st.columns([2, 1])

    with col_info:
        if planning_info:
            # Afficher les infos du planning sauvegardé
            created_at = planning_info.get('created_at', '')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    created_str = dt.strftime("%d/%m/%Y à %H:%M")
                except:
                    created_str = created_at

            st.success(f"💾 **Planning sauvegardé le {created_str}**")
            st.write(f"- Année: {planning_info.get('year')}")
            st.write(f"- Allocations: {planning_info.get('num_allocations')} internes, {planning_info.get('num_external')} externes")
            st.write(f"- Missions: {planning_info.get('num_missions')}")
            st.write(f"- Auditeurs: {planning_info.get('num_auditors')}")
        else:
            st.info("Aucun planning sauvegardé. Lancez une optimisation pour créer un planning.")

    with col_actions:
        # Bouton pour charger le planning sauvegardé
        if planning_info:
            if not result or not result.success:
                if st.button("📂 Charger le planning", type="primary"):
                    _load_planning_from_auto()
                    st.rerun()
            else:
                st.write("✅ Planning chargé en mémoire")

                # Export manuel
                if result:
                    export_data = {
                        'version': '2.0',
                        'created_at': datetime.now().isoformat(),
                        'year': st.session_state.get('year', auto_year),
                        'result': result.to_dict(),
                        'missions': [
                            {
                                'name': m.name,
                                'budget_days': m.budget_days,
                                'num_auditors': m.num_auditors,
                                'period_type': m.period_type.value if m.period_type else 'Trimestre',
                                'period_value': m.period_value,
                                'required_skills': [s.value for s in m.required_skills] if m.required_skills else ['Généraliste'],
                                'allowed_weeks': list(m.allowed_weeks),
                                'supervisor_name': m.supervisor_name
                            }
                            for m in st.session_state.get('missions', [])
                        ],
                        'auditors': [
                            {
                                'name': a.name,
                                'budget_days': a.budget_days,
                                'skills': [s.value for s in a.skills],
                                'unavailable_weeks': list(a.unavailable_weeks),
                                'is_external': a.is_external,
                                'is_supervisor': a.is_supervisor
                            }
                            for a in st.session_state.get('auditors', [])
                        ]
                    }
                    json_str = json.dumps(export_data, indent=2, ensure_ascii=False)
                    st.download_button(
                        label="📥 Exporter JSON",
                        data=json_str,
                        file_name=f"planning_{st.session_state.get('year', auto_year)}.json",
                        mime="application/json"
                    )

    st.markdown("---")

    # Section 2: Import manuel (optionnel)
    with st.expander("📁 Importer un planning externe (optionnel)"):
        uploaded_planning = st.file_uploader(
            "Sélectionner un fichier JSON",
            type=['json'],
            key='planning_upload'
        )

    if uploaded_planning:
        try:
            planning_data = json.load(uploaded_planning)

            st.success(f"Planning chargé: {planning_data.get('created_at', 'date inconnue')}")

            # Afficher un résumé
            result_data = planning_data.get('result', {})
            num_alloc = len(result_data.get('allocations', []))
            num_ext = len(result_data.get('external_allocations', []))
            st.write(f"- **Allocations internes:** {num_alloc}")
            st.write(f"- **Allocations externes:** {num_ext}")
            st.write(f"- **Missions:** {len(planning_data.get('missions', []))}")
            st.write(f"- **Auditeurs:** {len(planning_data.get('auditors', []))}")

            if st.button("Charger ce planning"):
                # Charger les missions
                missions = []
                for m in planning_data.get('missions', []):
                    # Gérer les deux formats: required_skill (ancien) et required_skills (nouveau)
                    skills = set()
                    if m.get('required_skill'):
                        skill = Skill.from_string(m['required_skill'])
                        if skill:
                            skills.add(skill)
                    elif m.get('required_skills'):
                        for s in m['required_skills']:
                            skill = Skill.from_string(s)
                            if skill:
                                skills.add(skill)
                    if not skills:
                        skills = {Skill.GENERALISTE}

                    missions.append(Mission(
                        name=m['name'],
                        budget_days=m['budget_days'],
                        num_auditors=m['num_auditors'],
                        required_skills=skills,
                        allowed_weeks=set(m.get('allowed_weeks', [])),
                        supervisor_name=m.get('supervisor_name')
                    ))

                auditors = []
                for a in planning_data.get('auditors', []):
                    skills = {Skill.from_string(s) for s in a.get('skills', []) if Skill.from_string(s)}
                    auditors.append(Auditor(
                        name=a['name'],
                        budget_days=a['budget_days'],
                        skills=skills,
                        unavailable_weeks=set(a.get('unavailable_weeks', [])),
                        is_external=a.get('is_external', False),
                        is_supervisor=a.get('is_supervisor', False)
                    ))

                # Charger le résultat
                loaded_result = OptimizationResult.from_dict(result_data)

                # Mettre à jour session_state
                st.session_state.missions = missions
                st.session_state.auditors = auditors
                st.session_state.result = loaded_result
                st.session_state.year = planning_data.get('year', 2025)

                st.success("Planning chargé avec succès!")
                st.rerun()

        except Exception as e:
            st.error(f"Erreur lors du chargement: {e}")

    st.markdown("---")

    # Section 3: Replanification
    st.subheader("3. Replanification à partir d'une semaine")

    if not st.session_state.get('result') or not st.session_state.result.success:
        st.info("Chargez d'abord un planning existant pour le replanifier.")
        return

    # Auto-détection de la semaine et année courantes
    auto_week = datetime.now().isocalendar()[1]
    auto_year = datetime.now().year

    col1, col2, col3 = st.columns(3)

    with col1:
        current_week = st.number_input(
            "Semaine actuelle",
            min_value=1,
            max_value=52,
            value=st.session_state.get('current_week', auto_week),
            help="Les allocations avant cette semaine seront conservées"
        )
        st.session_state.current_week = current_week

    with col2:
        planning_year = st.number_input(
            "Année",
            min_value=2020,
            max_value=2030,
            value=st.session_state.get('year', auto_year)
        )
        st.session_state.year = planning_year

    with col3:
        st.metric("Semaines à replanifier", f"S{current_week} → S52")
        st.caption(f"📅 Auto-détecté: S{auto_week}/{auto_year}")

    # Afficher les allocations verrouillées vs à replanifier
    result = st.session_state.result
    all_allocs = result.allocations + result.external_allocations

    locked_allocs = [a for a in all_allocs if a.week < current_week]
    future_allocs = [a for a in all_allocs if a.week >= current_week]

    st.write(f"**Allocations verrouillées (passé):** {len(locked_allocs)}")
    st.write(f"**Allocations à recalculer (futur):** {len(future_allocs)}")

    st.markdown("---")

    # Rappel: gérer les indisponibilités dans l'onglet Auditeurs
    st.info("💡 Pour gérer les congés, maladies et autres indisponibilités, utilisez l'onglet **Auditeurs**.")

    # Bouton de replanification
    st.subheader("4. Lancer la replanification")

    if st.button("🔄 Replanifier", type="primary"):
        # Synchroniser automatiquement les contraintes depuis les profils auditeurs
        year = st.session_state.get('year', auto_year)
        auditors_updated = auditor_manager.to_auditors(year)
        if auditors_updated:
            st.session_state.auditors = auditors_updated
            st.info(f"Contraintes synchronisées depuis {len(auditors_updated)} profils auditeurs")

        # Garder les allocations passées (verrouillées)
        locked_result = OptimizationResult(success=True)
        locked_result.allocations = [a for a in result.allocations if a.week < current_week]
        locked_result.external_allocations = [a for a in result.external_allocations if a.week < current_week]

        # Calculer le budget restant par mission
        missions_to_replan = []
        for mission in st.session_state.missions:
            # Jours déjà alloués (verrouillés)
            locked_days = sum(
                a.days for a in locked_result.allocations + locked_result.external_allocations
                if a.mission_name == mission.name
            )

            remaining_budget = mission.budget_days - locked_days
            if remaining_budget > 0:
                # Créer une mission avec le budget restant
                remaining_mission = Mission(
                    name=mission.name,
                    budget_days=remaining_budget,
                    num_auditors=mission.num_auditors,
                    required_skills=mission.required_skills,
                    allowed_weeks={w for w in mission.allowed_weeks if w >= current_week},
                    supervisor_name=mission.supervisor_name
                )
                missions_to_replan.append(remaining_mission)

        if not missions_to_replan:
            st.warning("Aucune mission à replanifier.")
            return

        st.info(f"Replanification de {len(missions_to_replan)} missions à partir de S{current_week}...")

        # Lancer l'optimisation pour le reste
        config = PlanningConfig(year=st.session_state.year)
        optimizer = AuditStaffingOptimizer(config)

        milp_gap = st.session_state.get('gap_tolerance', 0.10)
        milp_time = st.session_state.get('milp_time_limit', 300)
        max_sub = st.session_state.get('max_subcontracting_days', None)
        min_load = st.session_state.get('min_load_ratio', 0.80)

        new_result = optimizer.optimize(
            missions_to_replan,
            st.session_state.auditors,
            verbose=False,
            time_limit=milp_time,
            gap_tolerance=milp_gap,
            max_subcontracting_days=max_sub,
            min_load_ratio=min_load
        )

        if new_result.success:
            # Fusionner les résultats
            final_result = OptimizationResult(success=True)
            final_result.allocations = locked_result.allocations + new_result.allocations
            final_result.external_allocations = locked_result.external_allocations + new_result.external_allocations
            final_result.external_resources_needed = len(final_result.external_allocations) > 0

            st.session_state.result = final_result

            # Sauvegarde automatique
            save_planning_auto(final_result, st.session_state.missions,
                               st.session_state.auditors, st.session_state.year)

            st.success(f"Replanification terminée! {len(new_result.allocations)} nouvelles allocations.")
            st.rerun()
        else:
            st.error("La replanification a échoué. Vérifiez les contraintes.")


if __name__ == "__main__":
    main()
