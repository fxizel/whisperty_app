"""Whisperty — historique local des transcriptions (V2).

Stocke chaque dictée dans une base SQLite locale (module ``sqlite3`` standard,
aucune dépendance ni accès réseau). Permet de retrouver/recopier une dictée
passée depuis le menu tray.

Confidentialité : la base reste un simple fichier sur la machine, à côté de
``config.yaml`` par défaut. Désactivable via ``history.enabled: false``.

Concurrence : ``add()`` est appelé depuis le thread de transcription, la lecture
depuis le thread tray. Un unique ``threading.Lock`` sérialise tous les accès à la
connexion (ouverte avec ``check_same_thread=False``).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class HistoryEntry:
    """Une transcription archivée."""

    id: int
    timestamp: str
    text: str
    source: str            # "dictée" | "fichier"
    app: Optional[str]      # nom du process actif (ex. "Code.exe") ou fichier importé
    model: Optional[str]


class History:
    """Journal des transcriptions adossé à SQLite (création paresseuse du fichier)."""

    def __init__(
        self,
        path: Union[str, Path],
        max_entries: int = 200,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path)
        # Robustesse : une valeur mal typée (config.yaml manuel) ne doit pas faire
        # planter le démarrage — on retombe sur la borne par défaut.
        try:
            self.max_entries = max(0, int(max_entries))
        except (TypeError, ValueError):
            logger.warning("history.max_entries invalide (%r) ; 200 utilisé.", max_entries)
            self.max_entries = 200
        self.enabled = enabled
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        # Une fois fermé (à l'arrêt de l'app), add()/recent() deviennent des no-op :
        # un écrivain tardif (thread live) ne doit pas ROUVRIR la connexion après close().
        self._closed = False

    @classmethod
    def from_config(cls, config) -> "History":
        """Construit l'historique depuis la config (chemin résolu près de config.yaml)."""
        hc = config.history
        return cls(
            path=config.resolve(hc.path),
            max_entries=hc.max_entries,
            enabled=hc.enabled,
        )

    # -- connexion (paresseuse) ------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Ouvre la connexion au premier besoin et crée le schéma. Sous verrou."""
        if self._closed:
            # Backstop anti-réouverture : un écrivain tardif après close() est refusé
            # (erreur sqlite3 capturée par les appelants → no-op silencieux).
            raise sqlite3.ProgrammingError("Historique fermé.")
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False : la connexion est partagée entre threads, mais
            # tous les accès passent par self._lock (cf. add/recent/clear).
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    text      TEXT NOT NULL,
                    source    TEXT NOT NULL DEFAULT 'dictée',
                    app       TEXT,
                    model     TEXT
                )
                """
            )
            self._conn.commit()
        return self._conn

    # -- écriture --------------------------------------------------------------
    def add(
        self,
        text: str,
        *,
        source: str = "dictée",
        app: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """Archive une transcription. No-op si désactivé, fermé, ou texte vide."""
        if not self.enabled or self._closed or not text:
            return
        timestamp = datetime.now().isoformat(timespec="seconds")
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "INSERT INTO transcriptions (timestamp, text, source, app, model) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (timestamp, text, source, app, model),
                )
                self._prune(conn)
                conn.commit()
        except sqlite3.Error:
            # L'archivage ne doit jamais interrompre le pipeline de dictée.
            logger.warning("Écriture dans l'historique échouée.", exc_info=True)

    def _prune(self, conn: sqlite3.Connection) -> None:
        """Conserve uniquement les ``max_entries`` transcriptions les plus récentes."""
        if self.max_entries <= 0:
            return
        conn.execute(
            "DELETE FROM transcriptions WHERE id NOT IN ("
            "  SELECT id FROM transcriptions ORDER BY id DESC LIMIT ?"
            ")",
            (self.max_entries,),
        )

    # -- lecture ---------------------------------------------------------------
    def recent(self, limit: int = 10) -> list[HistoryEntry]:
        """Renvoie les dernières transcriptions, de la plus récente à la plus ancienne."""
        if not self.enabled or self._closed:
            return []
        try:
            with self._lock:
                conn = self._connect()
                rows = conn.execute(
                    "SELECT id, timestamp, text, source, app, model "
                    "FROM transcriptions ORDER BY id DESC LIMIT ?",
                    (max(0, int(limit)),),
                ).fetchall()
        except sqlite3.Error:
            logger.warning("Lecture de l'historique échouée.", exc_info=True)
            return []
        return [
            HistoryEntry(
                id=r["id"], timestamp=r["timestamp"], text=r["text"],
                source=r["source"], app=r["app"], model=r["model"],
            )
            for r in rows
        ]

    def last_text(self) -> Optional[str]:
        """Texte de la dernière transcription, ou ``None`` si l'historique est vide."""
        entries = self.recent(1)
        return entries[0].text if entries else None

    def delete(self, entry_id: int) -> None:
        """Supprime une transcription par son ``id``. No-op si désactivé/fermé/absent."""
        if not self.enabled or self._closed:
            return
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            logger.warning("Suppression historique : id invalide (%r).", entry_id)
            return
        try:
            with self._lock:
                conn = self._connect()
                conn.execute("DELETE FROM transcriptions WHERE id = ?", (entry_id,))
                conn.commit()
        except sqlite3.Error:
            logger.warning("Suppression d'une entrée d'historique échouée.", exc_info=True)

    def clear(self) -> None:
        """Vide l'historique."""
        if not self.enabled or self._closed:
            return
        try:
            with self._lock:
                conn = self._connect()
                conn.execute("DELETE FROM transcriptions")
                conn.commit()
        except sqlite3.Error:
            logger.warning("Purge de l'historique échouée.", exc_info=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
