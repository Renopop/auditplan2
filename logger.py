"""
Module de logging pour l'application d'audit planning.
Centralise tous les warnings et messages de debug.
"""

import logging
import os
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, field
from enum import Enum


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class LogEntry:
    timestamp: datetime
    level: LogLevel
    source: str
    message: str


class AuditLogger:
    """Logger centralisé pour l'application d'audit planning."""

    _instance: Optional['AuditLogger'] = None
    _entries: List[LogEntry] = field(default_factory=list)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._entries = []
            cls._instance._setup_file_logger()
        return cls._instance

    def _setup_file_logger(self):
        """Configure le logger fichier."""
        log_dir = os.path.join(os.path.dirname(__file__), 'logs')
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, f'audit_{datetime.now().strftime("%Y%m%d")}.log')

        self._file_logger = logging.getLogger('AuditPlan')
        self._file_logger.setLevel(logging.DEBUG)

        # Éviter les handlers dupliqués
        if not self._file_logger.handlers:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            self._file_logger.addHandler(file_handler)

    def clear(self):
        """Efface les logs en mémoire (pas le fichier)."""
        self._entries = []

    def _log(self, level: LogLevel, source: str, message: str):
        """Ajoute une entrée de log."""
        entry = LogEntry(
            timestamp=datetime.now(),
            level=level,
            source=source,
            message=message
        )
        self._entries.append(entry)

        # Log aussi dans le fichier
        log_msg = f"[{source}] {message}"
        if level == LogLevel.DEBUG:
            self._file_logger.debug(log_msg)
        elif level == LogLevel.INFO:
            self._file_logger.info(log_msg)
        elif level == LogLevel.WARNING:
            self._file_logger.warning(log_msg)
        elif level == LogLevel.ERROR:
            self._file_logger.error(log_msg)

    def debug(self, source: str, message: str):
        self._log(LogLevel.DEBUG, source, message)

    def info(self, source: str, message: str):
        self._log(LogLevel.INFO, source, message)

    def warning(self, source: str, message: str):
        self._log(LogLevel.WARNING, source, message)

    def error(self, source: str, message: str):
        self._log(LogLevel.ERROR, source, message)

    def get_entries(self, level: Optional[LogLevel] = None, source: Optional[str] = None) -> List[LogEntry]:
        """Récupère les entrées de log avec filtres optionnels."""
        entries = self._entries
        if level:
            entries = [e for e in entries if e.level == level]
        if source:
            entries = [e for e in entries if e.source == source]
        return entries

    def get_warnings(self) -> List[str]:
        """Récupère uniquement les warnings."""
        return [e.message for e in self._entries if e.level == LogLevel.WARNING]

    def get_errors(self) -> List[str]:
        """Récupère uniquement les erreurs."""
        return [e.message for e in self._entries if e.level == LogLevel.ERROR]

    def get_all_messages(self) -> List[str]:
        """Récupère tous les messages formatés."""
        return [
            f"[{e.timestamp.strftime('%H:%M:%S')}] [{e.level.value}] [{e.source}] {e.message}"
            for e in self._entries
        ]

    def has_warnings(self) -> bool:
        return any(e.level == LogLevel.WARNING for e in self._entries)

    def has_errors(self) -> bool:
        return any(e.level == LogLevel.ERROR for e in self._entries)


# Instance globale
logger = AuditLogger()
