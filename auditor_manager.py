"""
Gestionnaire de persistance des auditeurs.
Gère le chargement et la sauvegarde des profils d'auditeurs.
"""

import json
import os
from datetime import datetime
from typing import List, Optional, Dict
from models import (
    AuditorProfile, AuditorGrade, AuditorStatus,
    Unavailability, UnavailabilityType, Skill, Auditor
)


class AuditorManager:
    """Gestionnaire des profils d'auditeurs avec persistance JSON."""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            from paths import get_base_dir
            data_dir = os.path.join(get_base_dir(), 'data')
        self.data_dir = data_dir
        self.profiles_file = os.path.join(data_dir, 'auditor_profiles.json')
        self._profiles: List[AuditorProfile] = []
        self._load()

    def _load(self):
        """Charge les profils depuis le fichier JSON."""
        if not os.path.exists(self.profiles_file):
            self._profiles = []
            return

        try:
            with open(self.profiles_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self._profiles = [
                AuditorProfile.from_dict(p)
                for p in data.get('profiles', [])
            ]
        except Exception as e:
            print(f"Erreur chargement profils: {e}")
            self._profiles = []

    def _save(self) -> bool:
        """Sauvegarde les profils dans le fichier JSON."""
        os.makedirs(self.data_dir, exist_ok=True)

        try:
            data = {
                'version': '2.0',
                'last_updated': datetime.now().isoformat(),
                'profiles': [p.to_dict() for p in self._profiles]
            }

            with open(self.profiles_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"Erreur sauvegarde profils: {e}")
            return False

    def get_all(self) -> List[AuditorProfile]:
        """Retourne tous les profils."""
        return self._profiles.copy()

    def get_active(self, year: int = None) -> List[AuditorProfile]:
        """Retourne les auditeurs actifs pour une année donnée."""
        if year is None:
            year = datetime.now().year
        current_week = datetime.now().isocalendar()[1]

        active = []
        for p in self._profiles:
            p._update_status(current_week, year)
            if p.status == AuditorStatus.ACTIF:
                active.append(p)
        return active

    def get_by_name(self, name: str) -> Optional[AuditorProfile]:
        """Recherche un auditeur par nom."""
        for p in self._profiles:
            if p.name.lower() == name.lower():
                return p
        return None

    def get_by_status(self, status: AuditorStatus) -> List[AuditorProfile]:
        """Retourne les auditeurs avec un statut donné."""
        return [p for p in self._profiles if p.status == status]

    def get_by_grade(self, grade: AuditorGrade) -> List[AuditorProfile]:
        """Retourne les auditeurs d'un grade donné."""
        return [p for p in self._profiles if p.grade == grade]

    def add(self, profile: AuditorProfile) -> bool:
        """Ajoute un nouveau profil."""
        if self.get_by_name(profile.name):
            return False  # Déjà existant

        self._profiles.append(profile)
        return self._save()

    def update(self, name: str, profile: AuditorProfile) -> bool:
        """Met à jour un profil existant."""
        for i, p in enumerate(self._profiles):
            if p.name.lower() == name.lower():
                self._profiles[i] = profile
                return self._save()
        return False

    def delete(self, name: str) -> bool:
        """Supprime un profil."""
        for i, p in enumerate(self._profiles):
            if p.name.lower() == name.lower():
                self._profiles.pop(i)
                return self._save()
        return False

    def add_unavailability(self, auditor_name: str, unavailability: Unavailability) -> bool:
        """Ajoute une indisponibilité à un auditeur."""
        profile = self.get_by_name(auditor_name)
        if not profile:
            return False

        profile.unavailabilities.append(unavailability)
        return self._save()

    def remove_unavailability(self, auditor_name: str, index: int) -> bool:
        """Supprime une indisponibilité d'un auditeur."""
        profile = self.get_by_name(auditor_name)
        if not profile or index >= len(profile.unavailabilities):
            return False

        profile.unavailabilities.pop(index)
        return self._save()

    def set_departure(self, auditor_name: str, week: int, year: int) -> bool:
        """Définit la date de départ d'un auditeur."""
        profile = self.get_by_name(auditor_name)
        if not profile:
            return False

        profile.departure_week = week
        profile.departure_year = year
        profile._update_status()
        return self._save()

    def set_arrival(self, auditor_name: str, week: int, year: int) -> bool:
        """Définit la date d'arrivée d'un auditeur."""
        profile = self.get_by_name(auditor_name)
        if not profile:
            return False

        profile.arrival_week = week
        profile.arrival_year = year
        profile._update_status()
        return self._save()

    def to_auditors(self, year: int = None) -> List[Auditor]:
        """
        Convertit les profils actifs en objets Auditor pour l'optimiseur.
        Ne retourne que les auditeurs actifs.
        """
        if year is None:
            year = datetime.now().year

        auditors = []
        for profile in self.get_active(year):
            auditors.append(profile.to_auditor(year))
        return auditors

    def import_from_legacy(self, auditors: List[Auditor]) -> int:
        """
        Importe des auditeurs depuis l'ancien format (List[Auditor]).
        Retourne le nombre d'auditeurs importés.
        """
        imported = 0
        for auditor in auditors:
            if auditor.is_external:
                continue  # Ignorer les externes

            if self.get_by_name(auditor.name):
                continue  # Déjà existant

            profile = AuditorProfile(
                name=auditor.name,
                grade=AuditorGrade.CONFIRME,  # Par défaut
                budget_days=auditor.budget_days,
                skills=auditor.skills,
                is_supervisor=auditor.is_supervisor
            )

            # Convertir les semaines indisponibles en indisponibilité "Autre"
            if auditor.unavailable_weeks:
                weeks = sorted(auditor.unavailable_weeks)
                if weeks:
                    # Grouper les semaines consécutives
                    groups = []
                    current_group = [weeks[0]]
                    for w in weeks[1:]:
                        if w == current_group[-1] + 1:
                            current_group.append(w)
                        else:
                            groups.append(current_group)
                            current_group = [w]
                    groups.append(current_group)

                    for group in groups:
                        profile.unavailabilities.append(Unavailability(
                            type=UnavailabilityType.AUTRE,
                            start_week=group[0],
                            end_week=group[-1],
                            year=datetime.now().year,
                            reason="Importé depuis ancien format"
                        ))

            self._profiles.append(profile)
            imported += 1

        if imported > 0:
            self._save()
        return imported

    def get_statistics(self, year: int = None) -> Dict:
        """Retourne des statistiques sur les auditeurs."""
        if year is None:
            year = datetime.now().year

        stats = {
            'total': len(self._profiles),
            'actifs': len(self.get_by_status(AuditorStatus.ACTIF)),
            'futurs': len(self.get_by_status(AuditorStatus.FUTUR)),
            'partis': len(self.get_by_status(AuditorStatus.PARTI)),
            'par_grade': {},
            'total_budget_days': 0,
            'total_unavailable_days': 0
        }

        for grade in AuditorGrade:
            stats['par_grade'][grade.value] = len(self.get_by_grade(grade))

        for profile in self.get_active(year):
            stats['total_budget_days'] += profile.budget_days
            stats['total_unavailable_days'] += len(profile.get_unavailable_weeks(year)) * 5

        return stats


# Instance globale
auditor_manager = AuditorManager()
