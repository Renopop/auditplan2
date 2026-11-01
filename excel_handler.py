"""
Gestionnaire de fichiers Excel pour l'import/export des données d'audit.
"""

import pandas as pd
from typing import List, Dict, Tuple, Optional, Any
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from models import (
    Auditor, Mission, Allocation, OptimizationResult,
    PlanningConfig, Skill, PeriodType, get_weeks_for_period
)
from constraints import parse_unavailable_periods


class ExcelReader:
    """Lecteur de fichiers Excel pour les données d'entrée."""

    def __init__(self, file_path_or_buffer):
        """
        Initialise le lecteur Excel.

        Args:
            file_path_or_buffer: Chemin du fichier ou buffer (BytesIO)
        """
        self.file = file_path_or_buffer
        self.df_missions: Optional[pd.DataFrame] = None
        self.df_auditors: Optional[pd.DataFrame] = None

    def read_file(self) -> Dict[str, pd.DataFrame]:
        """
        Lit le fichier Excel et retourne les DataFrames.

        Returns:
            Dictionnaire {nom_feuille: DataFrame}
        """
        try:
            xlsx = pd.ExcelFile(self.file)
            sheets = {}
            for sheet_name in xlsx.sheet_names:
                sheets[sheet_name] = pd.read_excel(xlsx, sheet_name=sheet_name)
            return sheets
        except Exception as e:
            raise ValueError(f"Erreur lors de la lecture du fichier Excel: {e}")

    def parse_missions(
        self,
        df: pd.DataFrame,
        col_name: str,
        col_period: str,
        col_period_type: Optional[str],
        col_budget: str,
        col_supervisor: str,
        col_num_auditors: Optional[str],
        col_skills: Optional[str] = None,
        year: int = 2024
    ) -> List[Mission]:
        """
        Parse les missions depuis un DataFrame.

        Args:
            df: DataFrame contenant les données
            col_name: Nom de la colonne pour le nom de mission
            col_period: Nom de la colonne pour la période (Q1, Q2, Janvier, S12-S16, ...)
            col_period_type: Nom de la colonne pour le type de période (peut être None)
            col_budget: Nom de la colonne pour le budget
            col_supervisor: Nom de la colonne pour le superviseur
            col_num_auditors: Nom de la colonne pour le nombre d'auditeurs (peut être None)
            col_skills: Nom de la colonne pour les compétences requises
            year: Année de planification

        Returns:
            Liste de Mission
        """
        missions = []

        for _, row in df.iterrows():
            name = str(row.get(col_name, "")).strip()
            if not name or pd.isna(row.get(col_name)):
                continue

            # ---- Détermination du type de période ----
            period_value = str(row.get(col_period, "")).strip()
            period_lower = period_value.lower()

            if col_period_type and col_period_type in df.columns:
                # Utiliser la colonne explicite
                period_type_str = str(row.get(col_period_type, "Trimestre")).strip().lower()
                if "trim" in period_type_str or period_type_str.startswith("q"):
                    period_type = PeriodType.TRIMESTRE
                elif "mois" in period_type_str or "month" in period_type_str:
                    period_type = PeriodType.MOIS
                else:
                    period_type = PeriodType.SEMAINE
            else:
                # Inférence automatique à partir de la valeur de période
                if period_lower.startswith("q") or "trim" in period_lower:
                    period_type = PeriodType.TRIMESTRE
                elif any(m in period_lower for m in [
                    "jan", "janv", "janvier",
                    "fév", "fev", "févr", "février",
                    "mar", "mars",
                    "avr", "avril",
                    "mai",
                    "jun", "juin",
                    "jul", "juil", "juillet",
                    "août", "aout", "aug",
                    "sep", "sept", "septembre",
                    "oct", "octobre",
                    "nov", "novembre",
                    "déc", "dec", "décembre"
                ]):
                    period_type = PeriodType.MOIS
                else:
                    # Heuristique "semaine"
                    if period_lower.startswith("s") or "-" in period_lower or period_lower.isdigit():
                        period_type = PeriodType.SEMAINE
                    else:
                        # Fallback : trimestre
                        period_type = PeriodType.TRIMESTRE

            # ---- Budget ----
            try:
                budget = float(row.get(col_budget, 0))
            except (ValueError, TypeError):
                budget = 0

            # ---- Superviseur ----
            supervisor = str(row.get(col_supervisor, "")).strip()
            if pd.isna(row.get(col_supervisor)) or supervisor.lower() == "nan":
                supervisor = None

            # ---- Nombre d'auditeurs ----
            raw_num = 1
            if col_num_auditors and col_num_auditors in df.columns:
                raw_num = row.get(col_num_auditors, 1)
            try:
                num_auditors = int(raw_num)
            except (ValueError, TypeError):
                num_auditors = 1

            # ---- Compétences requises ----
            required_skills = set()
            if col_skills and col_skills in df.columns:
                skills_str = str(row.get(col_skills, "")).strip()
                if skills_str and skills_str.lower() != "nan":
                    for skill_name in skills_str.split(","):
                        skill = Skill.from_string(skill_name.strip())
                        if skill:
                            required_skills.add(skill)

            if not required_skills:
                required_skills = {Skill.GENERALISTE}

            # ---- Semaines autorisées ----
            allowed_weeks = get_weeks_for_period(period_type, period_value, year)

            # ---- Superviseur participe ? (colonne optionnelle, auto-détectée) ----
            supervisor_participates = False
            for cand in ("Superviseur Participe", "Superviseur participe",
                         "Sup Participe", "Participe"):
                if cand in df.columns:
                    val = row.get(cand)
                    if isinstance(val, bool):
                        supervisor_participates = val
                    elif isinstance(val, (int, float)) and not pd.isna(val):
                        supervisor_participates = (val == 1)
                    elif isinstance(val, str):
                        supervisor_participates = val.strip().lower() in (
                            "oui", "yes", "true", "1", "x")
                    break

            # ---- Chef de mission désigné (colonne optionnelle, auto-détectée) ----
            mission_lead = None
            for cand in ("Chef de Mission", "Chef de mission", "Chef Mission", "Chef"):
                if cand in df.columns:
                    v = str(row.get(cand, "")).strip()
                    if v and v.lower() != "nan":
                        mission_lead = v
                    break

            mission = Mission(
                name=name,
                budget_days=budget,
                period_type=period_type,
                period_value=period_value,
                required_skills=required_skills,
                supervisor_name=supervisor,
                num_auditors=num_auditors,
                allowed_weeks=allowed_weeks,
                supervisor_participates=supervisor_participates,
                mission_lead_name=mission_lead,
            )
            missions.append(mission)

        return missions

    def parse_auditors(
        self,
        df: pd.DataFrame,
        col_name: str,
        col_budget: str,
        col_skills: str,
        col_unavailable: Optional[str] = None,
        col_is_supervisor: Optional[str] = None,
        year: int = 2024
    ) -> List[Auditor]:
        """
        Parse les auditeurs depuis un DataFrame.

        Args:
            df: DataFrame contenant les données
            col_name: Nom de la colonne pour le nom
            col_budget: Nom de la colonne pour le budget jours
            col_skills: Nom de la colonne pour les compétences
            col_unavailable: Nom de la colonne pour les indisponibilités
            col_is_supervisor: Nom de la colonne indiquant si peut superviser
            year: Année de planification

        Returns:
            Liste d'Auditor
        """
        auditors = []

        for _, row in df.iterrows():
            name = str(row.get(col_name, "")).strip()
            if not name or pd.isna(row.get(col_name)):
                continue

            # Budget
            try:
                budget = float(row.get(col_budget, 205))
            except (ValueError, TypeError):
                budget = 205

            # Compétences
            skills = set()
            skills_str = str(row.get(col_skills, "")).strip()
            if skills_str and skills_str.lower() != "nan":
                for skill_name in skills_str.split(","):
                    skill = Skill.from_string(skill_name.strip())
                    if skill:
                        skills.add(skill)

            if not skills:
                skills = {Skill.GENERALISTE}

            # Indisponibilités
            unavailable_weeks = set()
            if col_unavailable and col_unavailable in df.columns:
                unavail_str = str(row.get(col_unavailable, "")).strip()
                if unavail_str and unavail_str.lower() != "nan":
                    unavailable_weeks = parse_unavailable_periods(unavail_str, year)

            # Peut superviser
            is_supervisor = False
            if col_is_supervisor and col_is_supervisor in df.columns:
                val = row.get(col_is_supervisor)
                if isinstance(val, bool):
                    is_supervisor = val
                elif isinstance(val, str):
                    is_supervisor = val.lower() in ("oui", "yes", "true", "1", "x")
                elif isinstance(val, (int, float)):
                    is_supervisor = val == 1

            auditor = Auditor(
                name=name,
                budget_days=budget,
                skills=skills,
                unavailable_weeks=unavailable_weeks,
                is_supervisor=is_supervisor
            )
            auditors.append(auditor)

        return auditors


class ExcelWriter:
    """Écrivain de fichiers Excel pour les résultats."""

    # Couleurs pour les auditeurs internes
    COLORS = [
        "C6EFCE",  # Vert clair
        "FFEB9C",  # Jaune clair
        "B4C6E7",  # Bleu clair
        "D9E1F2",  # Bleu très clair
        "E2EFDA",  # Vert très clair
        "FCE4D6",  # Orange clair
        "EDEDED",  # Gris clair
        "D9D9D9",  # Gris
        "F4B084",  # Orange
        "BDD7EE",  # Bleu ciel
    ]

    # Couleur pour sous-traitants
    EXTERNAL_COLOR = "FFC7CE"  # Rose clair

    def __init__(self):
        self.workbook = Workbook()

    def create_dashboard_sheet(
        self,
        result: OptimizationResult,
        missions: List[Mission],
        auditors: List[Auditor],
        sheet_name: str = "Tableau de Bord"
    ) -> None:
        """Crée un tableau de bord avec les KPIs principaux."""
        ws = self.workbook.active
        ws.title = sheet_name

        # Styles
        title_font = Font(bold=True, size=20, color="FFFFFF")
        title_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        header_font = Font(bold=True, size=12, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        kpi_font = Font(bold=True, size=14)
        kpi_value_font = Font(bold=True, size=24, color="2F5496")
        success_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        warning_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        external_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        center_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # Titre principal
        ws.merge_cells('A1:H1')
        title_cell = ws.cell(row=1, column=1, value="📊 TABLEAU DE BORD - PLAN D'AUDIT")
        title_cell.font = title_font
        title_cell.fill = title_fill
        title_cell.alignment = center_align
        ws.row_dimensions[1].height = 40

        # Calculs
        total_mission_days = sum(m.budget_days for m in missions)
        total_auditor_days = sum(a.budget_days for a in auditors)
        internal_days = sum(a.days for a in result.allocations)
        external_days = sum(a.days for a in result.external_allocations)
        num_subcontractors = len(set(a.auditor_name for a in result.external_allocations))

        # KPIs - Ligne 3
        row = 3
        kpis = [
            ("Missions", len(missions), "📋"),
            ("Auditeurs", len(auditors), "👥"),
            ("Jours Mission", f"{total_mission_days:.0f}", "📅"),
            ("Jours Dispo.", f"{total_auditor_days:.0f}", "✅"),
        ]

        for col, (label, value, icon) in enumerate(kpis, start=1):
            # Icône et label
            cell = ws.cell(row=row, column=col*2-1, value=f"{icon} {label}")
            cell.font = kpi_font
            cell.alignment = center_align
            ws.merge_cells(start_row=row, start_column=col*2-1, end_row=row, end_column=col*2)

            # Valeur
            cell = ws.cell(row=row+1, column=col*2-1, value=value)
            cell.font = kpi_value_font
            cell.alignment = center_align
            ws.merge_cells(start_row=row+1, start_column=col*2-1, end_row=row+1, end_column=col*2)

        # Statut de l'optimisation
        row = 6
        ws.merge_cells(f'A{row}:H{row}')
        status_text = "✅ OPTIMISATION RÉUSSIE" if result.success else "❌ ÉCHEC DE L'OPTIMISATION"
        status_cell = ws.cell(row=row, column=1, value=status_text)
        status_cell.font = Font(bold=True, size=16, color="FFFFFF")
        status_cell.fill = success_fill if result.success else warning_fill
        status_cell.alignment = center_align

        # Section Sous-traitance
        row = 8
        ws.merge_cells(f'A{row}:D{row}')
        ws.cell(row=row, column=1, value="📦 SOUS-TRAITANCE").font = Font(bold=True, size=14)

        row = 9
        sub_data = [
            ("Jours sous-traités", f"{external_days:.1f} j"),
            ("Nombre de sous-traitants", num_subcontractors),
            ("% du total", f"{(external_days/total_mission_days*100):.1f}%" if total_mission_days > 0 else "0%"),
        ]

        for i, (label, value) in enumerate(sub_data):
            ws.cell(row=row+i, column=1, value=label)
            cell = ws.cell(row=row+i, column=2, value=value)
            cell.font = Font(bold=True)
            if external_days > 0:
                cell.fill = external_fill

        # Section Répartition par auditeur
        row = 14
        ws.merge_cells(f'A{row}:D{row}')
        ws.cell(row=row, column=1, value="👥 CHARGE PAR AUDITEUR").font = Font(bold=True, size=14)

        row = 15
        headers = ["Auditeur", "Jours alloués", "Budget", "Utilisation"]
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border

        # Calculer les jours par auditeur
        auditor_days = {}
        for alloc in result.allocations:
            if alloc.auditor_name not in auditor_days:
                auditor_days[alloc.auditor_name] = 0
            auditor_days[alloc.auditor_name] += alloc.days

        row = 16
        for auditor in auditors:
            allocated = auditor_days.get(auditor.name, 0)
            utilization = (allocated / auditor.budget_days * 100) if auditor.budget_days > 0 else 0

            ws.cell(row=row, column=1, value=auditor.name).border = thin_border
            ws.cell(row=row, column=2, value=f"{allocated:.1f}").border = thin_border
            ws.cell(row=row, column=3, value=f"{auditor.budget_days:.0f}").border = thin_border

            util_cell = ws.cell(row=row, column=4, value=f"{utilization:.0f}%")
            util_cell.border = thin_border
            if utilization >= 90:
                util_cell.fill = warning_fill
            elif utilization >= 70:
                util_cell.fill = success_fill

            row += 1

        # Sous-traitants
        for alloc_name in set(a.auditor_name for a in result.external_allocations):
            ext_days = sum(a.days for a in result.external_allocations if a.auditor_name == alloc_name)
            ws.cell(row=row, column=1, value=f"🔸 {alloc_name}").border = thin_border
            ws.cell(row=row, column=2, value=f"{ext_days:.1f}").border = thin_border
            ws.cell(row=row, column=3, value="∞").border = thin_border
            cell = ws.cell(row=row, column=4, value="Externe")
            cell.border = thin_border
            cell.fill = external_fill
            row += 1

        # Ajuster les largeurs
        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 15
        ws.column_dimensions['F'].width = 15
        ws.column_dimensions['G'].width = 15
        ws.column_dimensions['H'].width = 15

    def create_planning_sheet(
        self,
        result: OptimizationResult,
        missions: List[Mission],
        sheet_name: str = "Planning"
    ) -> None:
        """
        Crée une feuille de planification avec le résultat.

        Format:
        - Lignes: Missions
        - Colonnes: Semaines 1-52
        - Cellules: Noms des auditeurs assignés
        """
        if sheet_name in self.workbook.sheetnames:
            ws = self.workbook[sheet_name]
        else:
            ws = self.workbook.create_sheet(sheet_name)

        # Styles
        title_font = Font(bold=True, size=16, color="FFFFFF")
        title_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=9)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        mission_font = Font(bold=True, size=10)
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center")
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        external_fill = PatternFill(start_color=self.EXTERNAL_COLOR, end_color=self.EXTERNAL_COLOR, fill_type="solid")

        # Titre
        ws.merge_cells('A1:E1')
        title_cell = ws.cell(row=1, column=1, value="📅 PLANNING ANNUEL DES MISSIONS")
        title_cell.font = title_font
        title_cell.fill = title_fill
        title_cell.alignment = center_align
        ws.row_dimensions[1].height = 30

        # En-têtes - ligne 3
        header_row = 3
        ws.cell(row=header_row, column=1, value="Mission").font = header_font
        ws.cell(row=header_row, column=1).fill = header_fill
        ws.cell(row=header_row, column=1).border = thin_border
        ws.cell(row=header_row, column=1).alignment = center_align

        ws.cell(row=header_row, column=2, value="Superviseur").font = header_font
        ws.cell(row=header_row, column=2).fill = header_fill
        ws.cell(row=header_row, column=2).border = thin_border
        ws.cell(row=header_row, column=2).alignment = center_align

        ws.cell(row=header_row, column=3, value="Budget").font = header_font
        ws.cell(row=header_row, column=3).fill = header_fill
        ws.cell(row=header_row, column=3).border = thin_border
        ws.cell(row=header_row, column=3).alignment = center_align

        for week in range(1, 53):
            col = week + 3
            cell = ws.cell(row=header_row, column=col, value=f"S{week}")
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

        # Récupérer la matrice de planning
        matrix = result.get_planning_matrix()

        # Liste de tous les auditeurs internes pour les couleurs
        internal_auditors = set()
        for mission_allocs in matrix.values():
            for week_allocs in mission_allocs.values():
                for name in week_allocs:
                    base_name = name.replace(" (S)", "").replace(" [EXT]", "")
                    if not base_name.startswith("Sous-traitant"):
                        internal_auditors.add(base_name)

        auditor_colors = {}
        for i, auditor in enumerate(sorted(internal_auditors)):
            auditor_colors[auditor] = self.COLORS[i % len(self.COLORS)]

        # Remplir les données
        row_idx = 4
        for mission in missions:
            # Nom de mission
            cell = ws.cell(row=row_idx, column=1, value=mission.name)
            cell.font = mission_font
            cell.border = thin_border
            cell.alignment = left_align

            # Superviseur
            cell = ws.cell(row=row_idx, column=2, value=mission.supervisor_name or "-")
            cell.border = thin_border
            cell.alignment = center_align

            # Budget
            cell = ws.cell(row=row_idx, column=3, value=f"{mission.budget_days:.0f}j")
            cell.border = thin_border
            cell.alignment = center_align

            if mission.name in matrix:
                for week, auditors in matrix[mission.name].items():
                    col = week + 3
                    # Afficher seulement les initiales pour gagner de la place
                    display_names = []
                    for name in auditors:
                        if "[EXT]" in name or name.startswith("Sous-traitant"):
                            display_names.append("🔸ST")
                        elif "(S)" in name:
                            base = name.replace(" (S)", "")
                            initials = "".join([p[0].upper() for p in base.split() if p])
                            display_names.append(f"{initials}*")
                        else:
                            initials = "".join([p[0].upper() for p in name.split() if p])
                            display_names.append(initials)

                    cell = ws.cell(row=row_idx, column=col, value="\n".join(display_names))
                    cell.alignment = center_align
                    cell.border = thin_border

                    # Colorer en fonction du premier auditeur
                    if auditors:
                        first_name = auditors[0].replace(" (S)", "").replace(" [EXT]", "")
                        if first_name.startswith("Sous-traitant"):
                            cell.fill = external_fill
                        else:
                            color = auditor_colors.get(first_name)
                            if color:
                                cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")

            row_idx += 1

        # Figer les volets (colonne A et lignes 1-3)
        ws.freeze_panes = 'D4'

        # Ajuster les largeurs
        ws.column_dimensions['A'].width = 28
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 8
        for week in range(1, 53):
            ws.column_dimensions[get_column_letter(week + 3)].width = 6

        # Ajuster la hauteur des lignes
        ws.row_dimensions[header_row].height = 25
        for row in range(4, row_idx):
            ws.row_dimensions[row].height = 25

        # Ajouter une légende en bas
        legend_row = row_idx + 2
        ws.cell(row=legend_row, column=1, value="Légende:").font = Font(bold=True)
        ws.cell(row=legend_row + 1, column=1, value="🔸ST = Sous-traitant")
        ws.cell(row=legend_row + 2, column=1, value="* = Superviseur de la mission")
        ws.cell(row=legend_row + 3, column=1, value="Initiales = Auditeur interne")

    def create_summary_sheet(
        self,
        result: OptimizationResult,
        missions: List[Mission],
        auditors: List[Auditor],
        sheet_name: str = "Résumé"
    ) -> None:
        """
        Crée une feuille de résumé avec statistiques.
        """
        if sheet_name in self.workbook.sheetnames:
            ws = self.workbook[sheet_name]
        else:
            ws = self.workbook.create_sheet(sheet_name)

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

        row = 1
        ws.cell(row=row, column=1, value="Résumé des missions").font = Font(bold=True, size=14)
        row += 2

        # Résumé des missions
        headers = ["Mission", "Budget (j)", "Auditeurs", "Semaines", "Total alloué (j)"]
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill

        from optimizer import AuditStaffingOptimizer
        optimizer = AuditStaffingOptimizer(PlanningConfig(year=2024))
        summary = optimizer.get_planning_summary(result)

        row += 1
        for mission in missions:
            stats = summary["missions"].get(mission.name, {
                "auditors": [],
                "weeks": [],
                "total_days": 0
            })
            ws.cell(row=row, column=1, value=mission.name)
            ws.cell(row=row, column=2, value=mission.budget_days)
            ws.cell(row=row, column=3, value=", ".join(stats["auditors"]))
            ws.cell(row=row, column=4, value=", ".join(f"S{w}" for w in stats["weeks"]))
            ws.cell(row=row, column=5, value=stats["total_days"])
            row += 1

        row += 2
        ws.cell(row=row, column=1, value="Résumé des auditeurs").font = Font(bold=True, size=14)
        row += 2

        headers = ["Auditeur", "Missions", "Total alloué (j)"]
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill

        row += 1
        for name, stats in summary["auditors"].items():
            ws.cell(row=row, column=1, value=name)
            ws.cell(row=row, column=2, value=", ".join(stats["missions"]))
            ws.cell(row=row, column=3, value=stats["total_days"])
            row += 1

    def create_legend_sheet(
        self,
        auditors: List[Auditor],
        sheet_name: str = "Légende"
    ) -> None:
        """
        Crée une feuille de légende (couleurs, symboles).
        """
        if sheet_name in self.workbook.sheetnames:
            ws = self.workbook[sheet_name]
        else:
            ws = self.workbook.create_sheet(sheet_name)

        ws.cell(row=1, column=1, value="Légende").font = Font(bold=True, size=16)

        ws.cell(row=3, column=1, value="Auditeur").font = Font(bold=True)
        ws.cell(row=3, column=2, value="Compétences").font = Font(bold=True)
        ws.cell(row=3, column=3, value="Budget (j)").font = Font(bold=True)

        for idx, auditor in enumerate(auditors, start=4):
            ws.cell(row=idx, column=1, value=auditor.name)
            skills = ", ".join(s.value for s in auditor.skills)
            ws.cell(row=idx, column=2, value=skills)
            ws.cell(row=idx, column=4, value=auditor.budget_days)

        # Légende des symboles
        row = len(auditors) + 6
        ws.cell(row=row, column=1, value="SYMBOLES").font = Font(bold=True, size=14)
        ws.cell(row=row + 2, column=1, value="(S)")
        ws.cell(row=row + 2, column=2, value="Chef de mission (parmi l'équipe affectée)")
        ws.cell(row=row + 3, column=1, value="[EXT]")
        ws.cell(row=row + 3, column=2, value="Ressource externe (sous-traitance)")
        ws.cell(row=row + 4, column=1, value="Superviseur")
        ws.cell(row=row + 4, column=2, value="Validateur de la mission (voir feuille Résumé)")

        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 15
        ws.column_dimensions['C'].width = 30
        ws.column_dimensions['D'].width = 12

    def create_auditor_planning_sheet(
        self,
        result: OptimizationResult,
        auditors: List[Auditor],
        sheet_name: str = "Planning Auditeurs"
    ) -> None:
        """
        Crée une feuille avec le planning annuel de tous les auditeurs.

        Format:
        - Lignes: Auditeurs
        - Colonnes: Semaines 1-52
        - Cellules: Nom de la mission
        """
        if sheet_name in self.workbook.sheetnames:
            ws = self.workbook[sheet_name]
        else:
            ws = self.workbook.create_sheet(sheet_name)

        # Styles
        title_font = Font(bold=True, size=16, color="FFFFFF")
        title_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=9)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        auditor_font = Font(bold=True, size=10)
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center")
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        external_fill = PatternFill(start_color=self.EXTERNAL_COLOR, end_color=self.EXTERNAL_COLOR, fill_type="solid")

        # Couleurs par mission
        mission_colors = {}
        all_missions = set()
        for alloc in result.allocations + result.external_allocations:
            all_missions.add(alloc.mission_name)
        for i, mission in enumerate(sorted(all_missions)):
            mission_colors[mission] = self.COLORS[i % len(self.COLORS)]

        # Titre
        ws.merge_cells('A1:E1')
        title_cell = ws.cell(row=1, column=1, value="📅 PLANNING ANNUEL DES AUDITEURS")
        title_cell.font = title_font
        title_cell.fill = title_fill
        title_cell.alignment = center_align
        ws.row_dimensions[1].height = 30

        # En-têtes - ligne 3
        header_row = 3
        ws.cell(row=header_row, column=1, value="Auditeur").font = header_font
        ws.cell(row=header_row, column=1).fill = header_fill
        ws.cell(row=header_row, column=1).border = thin_border
        ws.cell(row=header_row, column=1).alignment = center_align

        ws.cell(row=header_row, column=2, value="Budget").font = header_font
        ws.cell(row=header_row, column=2).fill = header_fill
        ws.cell(row=header_row, column=2).border = thin_border
        ws.cell(row=header_row, column=2).alignment = center_align

        ws.cell(row=header_row, column=3, value="Alloué").font = header_font
        ws.cell(row=header_row, column=3).fill = header_fill
        ws.cell(row=header_row, column=3).border = thin_border
        ws.cell(row=header_row, column=3).alignment = center_align

        for week in range(1, 53):
            col = week + 3
            cell = ws.cell(row=header_row, column=col, value=f"S{week}")
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

        # Construire le planning par auditeur
        # {auditor_name: {week: mission_name}}
        auditor_planning = {}
        auditor_days = {}

        for alloc in result.allocations + result.external_allocations:
            a = alloc.auditor_name
            if a not in auditor_planning:
                auditor_planning[a] = {}
                auditor_days[a] = 0
            auditor_planning[a][alloc.week] = alloc.mission_name
            auditor_days[a] += alloc.days

        # Remplir les données - d'abord les auditeurs internes
        # (les superviseurs/validateurs sont exclus -> feuille dédiée)
        row_idx = 4
        for auditor in auditors:
            if getattr(auditor, "is_supervisor", False):
                continue
            # Nom de l'auditeur
            cell = ws.cell(row=row_idx, column=1, value=auditor.name)
            cell.font = auditor_font
            cell.border = thin_border
            cell.alignment = left_align

            # Budget
            cell = ws.cell(row=row_idx, column=2, value=f"{auditor.budget_days:.0f}j")
            cell.border = thin_border
            cell.alignment = center_align

            # Jours alloués
            allocated = auditor_days.get(auditor.name, 0)
            cell = ws.cell(row=row_idx, column=3, value=f"{allocated:.0f}j")
            cell.border = thin_border
            cell.alignment = center_align

            # Planning par semaine
            if auditor.name in auditor_planning:
                for week, mission in auditor_planning[auditor.name].items():
                    col = week + 3
                    # Afficher le nom complet de la mission
                    cell = ws.cell(row=row_idx, column=col, value=mission)
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True, text_rotation=90)
                    cell.border = thin_border
                    cell.font = Font(size=8)

                    # Colorer par mission
                    color = mission_colors.get(mission)
                    if color:
                        cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")

            # Marquer les semaines d'indisponibilité
            for week in auditor.unavailable_weeks:
                if 1 <= week <= 52:
                    col = week + 3
                    cell = ws.cell(row=row_idx, column=col)
                    if cell.value is None:
                        cell.value = "X"
                        cell.alignment = center_align
                        cell.border = thin_border
                        cell.fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")

            row_idx += 1

        # Ajouter les sous-traitants
        subcontractors = set(a.auditor_name for a in result.external_allocations)
        for sub in sorted(subcontractors):
            # Nom du sous-traitant
            cell = ws.cell(row=row_idx, column=1, value=f"🔸 {sub}")
            cell.font = auditor_font
            cell.border = thin_border
            cell.alignment = left_align
            cell.fill = external_fill

            # Budget
            cell = ws.cell(row=row_idx, column=2, value="∞")
            cell.border = thin_border
            cell.alignment = center_align

            # Jours alloués
            allocated = auditor_days.get(sub, 0)
            cell = ws.cell(row=row_idx, column=3, value=f"{allocated:.0f}j")
            cell.border = thin_border
            cell.alignment = center_align

            # Planning par semaine
            if sub in auditor_planning:
                for week, mission in auditor_planning[sub].items():
                    col = week + 3
                    # Afficher le nom complet de la mission
                    cell = ws.cell(row=row_idx, column=col, value=mission)
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True, text_rotation=90)
                    cell.border = thin_border
                    cell.font = Font(size=8)
                    cell.fill = external_fill

            row_idx += 1

        # Figer les volets
        ws.freeze_panes = 'D4'

        # Ajuster les largeurs
        ws.column_dimensions['A'].width = 22
        ws.column_dimensions['B'].width = 8
        ws.column_dimensions['C'].width = 8
        # Colonnes des semaines plus larges pour le nom complet vertical
        for week in range(1, 53):
            ws.column_dimensions[get_column_letter(week + 3)].width = 4

        # Ajuster la hauteur des lignes pour afficher le texte vertical
        ws.row_dimensions[header_row].height = 25
        for row in range(4, row_idx):
            ws.row_dimensions[row].height = 80  # Plus haut pour texte vertical

    def create_supervisor_planning_sheet(
        self,
        result: OptimizationResult,
        missions: List[Mission],
        auditors: List[Auditor],
        sheet_name: str = "Planning Superviseurs"
    ) -> None:
        """
        Crée une feuille dédiée au planning des SUPERVISEURS (validateurs),
        séparée de celle des auditeurs exécutants.

        Format:
        - Lignes: Superviseurs
        - Colonnes: Semaines 1-52
        - Cellules: missions validées actives cette semaine ( « (P) » si le
          superviseur participe aussi comme ressource).
        """
        if sheet_name in self.workbook.sheetnames:
            ws = self.workbook[sheet_name]
        else:
            ws = self.workbook.create_sheet(sheet_name)

        title_font = Font(bold=True, size=16, color="FFFFFF")
        title_fill = PatternFill(start_color="7030A0", end_color="7030A0", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=9)
        header_fill = PatternFill(start_color="7030A0", end_color="7030A0", fill_type="solid")
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        left_align = Alignment(horizontal="left", vertical="center")
        thin_border = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'), bottom=Side(style='thin')
        )

        # Semaines actives par mission (d'après les allocations)
        mission_weeks = {}
        for alloc in result.allocations + result.external_allocations:
            mission_weeks.setdefault(alloc.mission_name, set()).add(alloc.week)

        # Planning des superviseurs : {sup: {semaine: [libellés]}}
        planning = {}
        for m in missions:
            sup = m.supervisor_name
            if not sup:
                continue
            label = m.name + (" (P)" if getattr(m, "supervisor_participates", False) else "")
            for w in mission_weeks.get(m.name, set()):
                planning.setdefault(sup, {}).setdefault(w, []).append(label)

        # Liste ordonnée des superviseurs (déclarés OU validateurs de missions)
        sup_list = sorted(set(
            [a.name for a in auditors if getattr(a, "is_supervisor", False)]
            + list(planning.keys())
        ))

        # Titre
        ws.merge_cells('A1:C1')
        title_cell = ws.cell(row=1, column=1, value="🛡️ PLANNING DES SUPERVISEURS (VALIDATEURS)")
        title_cell.font = title_font
        title_cell.fill = title_fill
        title_cell.alignment = center_align
        ws.row_dimensions[1].height = 30

        # En-têtes
        header_row = 3
        ws.cell(row=header_row, column=1, value="Superviseur").font = header_font
        ws.cell(row=header_row, column=1).fill = header_fill
        ws.cell(row=header_row, column=1).border = thin_border
        ws.cell(row=header_row, column=1).alignment = center_align
        ws.cell(row=header_row, column=2, value="Missions").font = header_font
        ws.cell(row=header_row, column=2).fill = header_fill
        ws.cell(row=header_row, column=2).border = thin_border
        ws.cell(row=header_row, column=2).alignment = center_align
        for week in range(1, 53):
            cell = ws.cell(row=header_row, column=week + 2, value=f"S{week}")
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

        sup_fill = PatternFill(start_color="E4DFEC", end_color="E4DFEC", fill_type="solid")
        row_idx = 4
        for sup in sup_list:
            weeks_map = planning.get(sup, {})
            n_missions = len(set(
                lbl.replace(" (P)", "")
                for labels in weeks_map.values() for lbl in labels
            ))

            cell = ws.cell(row=row_idx, column=1, value=sup)
            cell.font = Font(bold=True, size=10)
            cell.border = thin_border
            cell.alignment = left_align
            cell.fill = sup_fill

            cell = ws.cell(row=row_idx, column=2, value=n_missions)
            cell.border = thin_border
            cell.alignment = center_align

            for week in range(1, 53):
                col = week + 2
                labels = weeks_map.get(week, [])
                cell = ws.cell(row=row_idx, column=col)
                cell.border = thin_border
                if labels:
                    cell.value = "\n".join(labels)
                    cell.alignment = Alignment(horizontal='center', vertical='center',
                                               wrap_text=True, text_rotation=90)
                    cell.font = Font(size=7)
                    cell.fill = sup_fill
                else:
                    cell.alignment = center_align
            row_idx += 1

        ws.freeze_panes = 'C4'
        ws.column_dimensions['A'].width = 22
        ws.column_dimensions['B'].width = 9
        for week in range(1, 53):
            ws.column_dimensions[get_column_letter(week + 2)].width = 4
        ws.row_dimensions[header_row].height = 25
        for row in range(4, row_idx):
            ws.row_dimensions[row].height = 80

    def save(self, file_path_or_buffer) -> None:
        """Sauvegarde le workbook."""
        self.workbook.save(file_path_or_buffer)

    def get_bytes(self) -> bytes:
        """Retourne le workbook en bytes pour téléchargement."""
        buffer = BytesIO()
        self.workbook.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()


def create_template_excel(file_path: str) -> None:
    """
    Crée un fichier Excel template avec les colonnes requises.
    """
    wb = Workbook()

    # Feuille Missions
    ws_missions = wb.active
    ws_missions.title = "Missions"

    mission_headers = [
        "Nom Mission",
        "Période",
        "Budget (jours)",
        "Superviseur",
        "Superviseur Participe",
        "Nb Auditeurs",
        "Compétences Requises"
    ]
    for col, header in enumerate(mission_headers, start=1):
        cell = ws_missions.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        cell.font = Font(bold=True, color="FFFFFF")

    # Exemples de missions (Superviseur = validateur ; "Superviseur Participe"
    # = Oui pour l'allouer ponctuellement comme ressource de la mission)
    example_missions = [
        ("Audit Crédit Immobilier", "Q1", 30, "Martin", "Non", 2, "Assurance"),
        ("Audit Sécurité IT", "Mars-Avril", 45, "Dupont", "Non", 3, "IT"),
        ("Revue Modèle PD", "S15-S20", 25, "Bernard", "Oui", 2, "Modèle"),
        ("Audit Conformité MIF2", "Q2", 20, "Martin", "Non", 1, "Assurance, Généraliste"),
    ]

    for row, mission in enumerate(example_missions, start=2):
        for col, value in enumerate(mission, start=1):
            ws_missions.cell(row=row, column=col, value=value)

    # Ajuster largeurs
    for col in range(1, 8):
        ws_missions.column_dimensions[get_column_letter(col)].width = 20

    # Feuille Auditeurs
    ws_auditors = wb.create_sheet("Auditeurs")

    auditor_headers = [
        "Nom",
        "Budget (jours)",
        "Compétences",
        "Indisponibilités",
        "Peut Superviser"
    ]
    for col, header in enumerate(auditor_headers, start=1):
        cell = ws_auditors.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        cell.font = Font(bold=True, color="FFFFFF")
        ws_auditors.column_dimensions[get_column_letter(col)].width = 20

    wb.save(file_path)
