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

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Version courante du schéma (PRAGMA user_version). Toute évolution du schéma passe
# par une migration incrémentale dans _migrate() — jamais par un DROP/recreate.
_SCHEMA_VERSION = 1


@dataclass
class HistoryEntry:
    """Une transcription archivée."""

    id: int
    timestamp: str
    text: str
    source: str            # "dictée" | "fichier"
    app: Optional[str]      # nom du process actif (ex. "Code.exe") ou fichier importé
    model: Optional[str]
    # Structure de session (réunion diarisée, FR-31) : segments à clés de locuteur +
    # registre des libellés, pour le renommage post-session. None pour une dictée.
    payload: Optional[dict] = field(default=None)


class History:
    """Journal des transcriptions adossé à SQLite (création paresseuse du fichier)."""

    def __init__(
        self,
        path: Union[str, Path],
        max_entries: int = 200,
        enabled: bool = True,
        max_age_days: int = 0,
    ) -> None:
        self.path = Path(path)
        # Robustesse : une valeur mal typée (config.yaml manuel) ne doit pas faire
        # planter le démarrage — on retombe sur la borne par défaut.
        try:
            self.max_entries = max(0, int(max_entries))
        except (TypeError, ValueError):
            logger.warning("history.max_entries invalide (%r) ; 200 utilisé.", max_entries)
            self.max_entries = 200
        # Rétention temporelle (RGPD) : 0 = illimité (défaut, comportement historique).
        try:
            self.max_age_days = max(0, int(max_age_days))
        except (TypeError, ValueError):
            logger.warning("history.max_age_days invalide (%r) ; 0 (illimité) utilisé.", max_age_days)
            self.max_age_days = 0
        self.enabled = enabled
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        # Index plein texte FTS5 disponible ? (déterminé à la connexion ; repli LIKE sinon)
        self._fts_ok = False
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
            max_age_days=getattr(hc, "max_age_days", 0),
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
            self._migrate(self._conn)
            self._fts_ok = self._ensure_fts(self._conn)
            # Purge temporelle dès l'ouverture : les entrées expirées disparaissent
            # même si aucune nouvelle dictée n'est archivée ensuite.
            self._prune(self._conn)
            self._conn.commit()
        return self._conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Migrations de schéma incrémentales, versionnées par ``PRAGMA user_version``.

        Chaque palier est idempotent (test de présence avant ALTER) : une base plus
        ancienne que l'application est mise à niveau sans perte ; une base déjà à jour
        est laissée telle quelle. Appelée sous ``self._lock`` (via ``_connect``).
        """
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 1:
            # v1 (FR-31) : colonne ``payload`` — structure JSON de session (segments à
            # clés de locuteur + registre des libellés) pour le renommage post-session.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(transcriptions)")}
            if "payload" not in cols:
                conn.execute("ALTER TABLE transcriptions ADD COLUMN payload TEXT")
        if version < _SCHEMA_VERSION:
            # PRAGMA n'accepte pas de paramètre lié : la valeur est un littéral contrôlé.
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION:d}")

    def _ensure_fts(self, conn: sqlite3.Connection) -> bool:
        """Crée (si absent) l'index plein texte FTS5, synchronisé par triggers.

        FTS5 est compilé dans le sqlite3 des builds CPython (Windows comme CI Linux) ;
        en son absence, renvoie False et :meth:`search` retombe sur un LIKE.
        ``remove_diacritics 2`` : « reunion » retrouve « réunion ».
        """
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5("
                "text, content='transcriptions', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS transcriptions_fts_ai "
                "AFTER INSERT ON transcriptions BEGIN "
                "INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text); END"
            )
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS transcriptions_fts_ad "
                "AFTER DELETE ON transcriptions BEGIN "
                "INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) "
                "VALUES ('delete', old.id, old.text); END"
            )
            # Le renommage post-session (FR-31) réécrit ``text`` par UPDATE : sans ce
            # trigger, l'index FTS divergerait de la table (recherche périmée).
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS transcriptions_fts_au "
                "AFTER UPDATE OF text ON transcriptions BEGIN "
                "INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) "
                "VALUES ('delete', old.id, old.text); "
                "INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text); END"
            )
            # Base créée AVANT l'index (mise à jour de l'app) : reconstruction unique
            # si l'index est en retard sur la table (idempotent, coût minime à l'échelle
            # de max_entries).
            n_rows = conn.execute("SELECT COUNT(*) FROM transcriptions").fetchone()[0]
            n_fts = conn.execute("SELECT COUNT(*) FROM transcriptions_fts").fetchone()[0]
            if n_fts != n_rows:
                conn.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES ('rebuild')")
            return True
        except sqlite3.Error:
            logger.warning("FTS5 indisponible : la recherche retombera sur LIKE.", exc_info=True)
            return False

    # -- écriture --------------------------------------------------------------
    @staticmethod
    def _dump_payload(payload: Optional[dict]) -> Optional[str]:
        """Sérialise la structure de session en JSON, ou ``None`` (never-fail)."""
        if not payload:
            return None
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            logger.warning("Structure de session non sérialisable ; archivée sans.", exc_info=True)
            return None

    @staticmethod
    def _load_payload(raw: object) -> Optional[dict]:
        """Désérialise la colonne ``payload``, ou ``None`` (donnée absente/corrompue)."""
        if not raw:
            return None
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError):
            logger.warning("Structure de session illisible dans l'historique.", exc_info=True)
            return None
        return data if isinstance(data, dict) else None

    def add(
        self,
        text: str,
        *,
        source: str = "dictée",
        app: Optional[str] = None,
        model: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        """Archive une transcription. No-op si désactivé, fermé, ou texte vide.

        ``payload`` (optionnel, FR-31) : structure de session JSON-compatible d'une
        réunion diarisée, permettant le renommage des locuteurs après la session.
        """
        if not self.enabled or self._closed or not text:
            return
        timestamp = datetime.now().isoformat(timespec="seconds")
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "INSERT INTO transcriptions (timestamp, text, source, app, model, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (timestamp, text, source, app, model, self._dump_payload(payload)),
                )
                self._prune(conn)
                conn.commit()
        except (sqlite3.Error, OSError):
            # L'archivage ne doit jamais interrompre le pipeline de dictée.
            # OSError couvre l'échec du mkdir de _connect (dossier en lecture seule…).
            logger.warning("Écriture dans l'historique échouée.", exc_info=True)

    def update_text(
        self, entry_id: int, text: str, payload: Optional[dict] = None,
    ) -> bool:
        """Réécrit le texte (et la structure de session) d'une entrée archivée (FR-31).

        L'index FTS suit via le trigger ``AFTER UPDATE``. ``payload=None`` laisse la
        colonne inchangée. Renvoie ``False`` (sans lever) si l'entrée n'existe pas,
        si l'historique est désactivé/fermé ou en cas d'erreur SQLite.
        """
        if not self.enabled or self._closed or not text:
            return False
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            logger.warning("Mise à jour historique : id invalide (%r).", entry_id)
            return False
        try:
            with self._lock:
                conn = self._connect()
                if payload is None:
                    cur = conn.execute(
                        "UPDATE transcriptions SET text = ? WHERE id = ?", (text, entry_id)
                    )
                else:
                    cur = conn.execute(
                        "UPDATE transcriptions SET text = ?, payload = ? WHERE id = ?",
                        (text, self._dump_payload(payload), entry_id),
                    )
                conn.commit()
                return cur.rowcount > 0
        except (sqlite3.Error, OSError):
            logger.warning("Mise à jour d'une entrée d'historique échouée.", exc_info=True)
            return False

    def _prune(self, conn: sqlite3.Connection) -> None:
        """Applique les deux rétentions : nombre max d'entrées et âge max (RGPD).

        Les suppressions passent par DELETE, donc les triggers tiennent l'index FTS
        à jour. ``timestamp`` est un isoformat local : la comparaison lexicale suffit
        (le seuil est calculé en Python, même horloge que l'écriture).
        """
        if self.max_entries > 0:
            conn.execute(
                "DELETE FROM transcriptions WHERE id NOT IN ("
                "  SELECT id FROM transcriptions ORDER BY id DESC LIMIT ?"
                ")",
                (self.max_entries,),
            )
        if self.max_age_days > 0:
            cutoff = (datetime.now() - timedelta(days=self.max_age_days)).isoformat(
                timespec="seconds"
            )
            conn.execute("DELETE FROM transcriptions WHERE timestamp < ?", (cutoff,))

    # -- lecture ---------------------------------------------------------------
    @classmethod
    def _entry(cls, r: sqlite3.Row) -> HistoryEntry:
        """Construit une entrée depuis une ligne SQL (payload désérialisé, tolérant)."""
        return HistoryEntry(
            id=r["id"], timestamp=r["timestamp"], text=r["text"],
            source=r["source"], app=r["app"], model=r["model"],
            payload=cls._load_payload(r["payload"]),
        )

    def recent(self, limit: int = 10) -> list[HistoryEntry]:
        """Renvoie les dernières transcriptions, de la plus récente à la plus ancienne."""
        if not self.enabled or self._closed:
            return []
        try:
            with self._lock:
                conn = self._connect()
                rows = conn.execute(
                    "SELECT id, timestamp, text, source, app, model, payload "
                    "FROM transcriptions ORDER BY id DESC LIMIT ?",
                    (max(0, int(limit)),),
                ).fetchall()
        except (sqlite3.Error, OSError):
            logger.warning("Lecture de l'historique échouée.", exc_info=True)
            return []
        return [self._entry(r) for r in rows]

    def get(self, entry_id: int) -> Optional[HistoryEntry]:
        """Renvoie une entrée par son ``id`` (payload compris), ou ``None``."""
        if not self.enabled or self._closed:
            return None
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            return None
        try:
            with self._lock:
                conn = self._connect()
                row = conn.execute(
                    "SELECT id, timestamp, text, source, app, model, payload "
                    "FROM transcriptions WHERE id = ?",
                    (entry_id,),
                ).fetchone()
        except (sqlite3.Error, OSError):
            logger.warning("Lecture d'une entrée d'historique échouée.", exc_info=True)
            return None
        return self._entry(row) if row is not None else None

    def last_text(self) -> Optional[str]:
        """Texte de la dernière transcription, ou ``None`` si l'historique est vide."""
        entries = self.recent(1)
        return entries[0].text if entries else None

    def search(self, query: str, limit: int = 200) -> list[HistoryEntry]:
        """Recherche plein texte dans l'historique, plus récentes d'abord.

        FTS5 quand disponible (mots entiers et préfixes, accents ignorés — « reunion »
        retrouve « réunion ») ; repli sous-chaîne LIKE sinon. Never-fail : requête
        vide, historique désactivé/fermé ou erreur → liste vide.
        """
        terms = str(query or "").split()
        if not terms or not self.enabled or self._closed:
            return []
        try:
            limit = max(0, int(limit))
        except (TypeError, ValueError):
            limit = 200
        try:
            with self._lock:
                conn = self._connect()
                if self._fts_ok:
                    # Chaque terme cité (neutralise la syntaxe FTS : OR, NEAR, parenthèses…)
                    # et en recherche par préfixe — « budg » trouve « budget ».
                    match = " ".join(
                        f'"{t}"*' for t in (t.replace('"', "") for t in terms) if t
                    )
                    if not match:
                        return []
                    rows = conn.execute(
                        "SELECT t.id, t.timestamp, t.text, t.source, t.app, t.model, t.payload "
                        "FROM transcriptions_fts f JOIN transcriptions t ON t.id = f.rowid "
                        "WHERE transcriptions_fts MATCH ? ORDER BY t.id DESC LIMIT ?",
                        (match, limit),
                    ).fetchall()
                else:
                    like = "%" + " ".join(terms).replace("\\", "\\\\").replace(
                        "%", "\\%"
                    ).replace("_", "\\_") + "%"
                    rows = conn.execute(
                        "SELECT id, timestamp, text, source, app, model, payload "
                        "FROM transcriptions "
                        "WHERE text LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                        (like, limit),
                    ).fetchall()
        except (sqlite3.Error, OSError):
            logger.warning("Recherche dans l'historique échouée.", exc_info=True)
            return []
        return [self._entry(r) for r in rows]

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
        except (sqlite3.Error, OSError):
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
        except (sqlite3.Error, OSError):
            logger.warning("Purge de l'historique échouée.", exc_info=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
