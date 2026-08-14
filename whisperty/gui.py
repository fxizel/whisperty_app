"""Whisperty — interface fenêtre (WebView2 via pywebview), V2.

Rend la maquette ``whisperty/web/`` dans une fenêtre native et l'adosse à
``WhispertyApp`` par un pont Python↔JS (``GuiApi``, exposé comme ``js_api``).
La fenêtre est un **compagnon** du tray : la fermer (croix) la masque dans la zone
de notification ; « Quitter » depuis le tray ferme réellement l'application.

Contraintes du projet :
- **100 % local** : aucun asset distant. La maquette chargeait Google Fonts ;
  c'est retiré (police système). Aucun fetch réseau côté JS.
- **Dépendance optionnelle** : ``pywebview`` est importé paresseusement ;
  s'il est absent (ou WebView2 indisponible), l'app retombe sur le mode tray seul
  (cf. ``WhispertyApp.run``). Voir ``gui.enabled`` dans config.yaml.
- **Threads** : ``webview.start()`` tient le thread principal ; le tray tourne en
  thread détaché. Les méthodes ``GuiApi`` sont appelées depuis le thread du pont et
  délèguent à ``WhispertyApp`` (transitions déjà sérialisées par ``_lock``).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .modeldl import model_size_name
from .version import __version__

if TYPE_CHECKING:
    from .app import WhispertyApp

logger = logging.getLogger(__name__)

_MONTHS_FR = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]


def web_dir() -> Path:
    """Dossier des assets web, en exécution source ou figée (PyInstaller)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        for cand in (base / "whisperty" / "web", base / "web"):
            if cand.is_dir():
                return cand
    return Path(__file__).resolve().parent / "web"


def _fmt_time(iso: Optional[str]) -> str:
    """Formate un timestamp ISO en libellé court FR (« Aujourd'hui 15:42 »)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    today = datetime.now().date()
    d = dt.date()
    hm = dt.strftime("%H:%M")
    delta = (today - d).days
    if delta == 0:
        return f"Aujourd'hui {hm}"
    if delta == 1:
        return f"Hier {hm}"
    return f"{dt.day} {_MONTHS_FR[dt.month - 1]} {hm}"


class GuiApi:
    """Pont exposé au JavaScript (``window.pywebview.api.*``).

    Seules les méthodes publiques (sans underscore) sont visibles côté JS.
    """

    def __init__(self, app: "WhispertyApp") -> None:
        # IMPORTANT : ces deux références sont PRIVÉES (préfixe _) À DESSEIN. pywebview
        # introspecte l'objet js_api (dir() + getattr) pour exposer ses méthodes et
        # RÉCURSE dans tout attribut public non-callable (webview/util.py get_functions).
        # Exposer `window`/`app` ferait parcourir tout le graphe natif (Window → WinForms
        # → WebView2) HORS du thread UI → tempête d'erreurs E_NOINTERFACE + récursion
        # infinie sur Rectangle.Empty. Les garder privés = pywebview les ignore.
        self._app = app
        self._window = None       # renseigné par launch_gui
        self._mode = "dictee"     # dictée | live | conference (bouton du dashboard)
        # Source audio choisie pour les modes loopback (live/conférence) : None = sortie
        # par défaut, sinon index dans list_speakers(). Éphémère (par démarrage), comme
        # le choix du sous-menu tray — n'est pas persisté dans config.yaml.
        self._source = None
        self._maximized = False
        # Première réduction dans le tray déjà signalée ? (notification unique par
        # session : « l'app reste active » — sinon l'utilisateur croit l'avoir fermée.)
        self._hide_notified = False

    # -- contrôles fenêtre -----------------------------------------------------
    def win_minimize(self) -> dict:
        return self._safe(lambda: self._window.minimize())

    def win_maximize(self) -> dict:
        def toggle():
            if self._maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._maximized = not self._maximized
        return self._safe(toggle)

    def win_close(self) -> dict:
        # Fermeture = masquer dans le tray (l'app continue d'écouter le raccourci).
        def hide():
            self._window.hide()
            self.notify_hidden_once()
        return self._safe(hide)

    def notify_hidden_once(self) -> None:
        """Signale (une seule fois par session) que la croix ne quitte pas l'app.

        Sans ce repère, l'utilisateur croit avoir fermé Whisperty et s'étonne que le
        raccourci continue de dicter (ou relance l'exe → cf. instance unique).
        """
        if self._hide_notified:
            return
        self._hide_notified = True
        try:
            self._app._notify_user(
                "Whisperty reste actif en arrière-plan — le raccourci de dictée "
                "fonctionne toujours. Rouvrez ou quittez via l'icône de la zone de "
                "notification.",
                "info",
            )
        except Exception:  # noqa: BLE001 — purement informatif
            logger.debug("Notification de réduction indisponible.", exc_info=True)


    def win_move(self, x: int, y: int) -> dict:
        """Déplace la fenêtre à la position absolue (x, y) en pixels écran."""
        return self._safe(lambda: self._window.move(int(x), int(y)))

    def get_version(self) -> dict:
        """Numéro de version de l'application (source unique : whisperty.version)."""
        return {"version": __version__}

    # -- état / dashboard ------------------------------------------------------
    def poll(self) -> dict:
        """État courant + niveau RMS + révisions (appelé ~5×/s par le JS).

        Payload volontairement minimal (modèle polling) : ``liveRev`` et ``noticeRev``
        sont des compteurs monotones — le JS ne récupère le contenu (``get_live_text``,
        ``get_notice``) que lorsqu'ils changent. ``modelOk`` pilote la bannière de
        téléchargement du modèle ; ``modelLoaded`` distingue « Chargement du modèle… »
        de « Transcription… » pendant l'état PROCESSING.
        """
        try:
            with self._app._lock:
                state = self._app._state.value
        except Exception:  # noqa: BLE001
            state = "idle"
        try:
            level = float(self._app.recorder.current_level)
        except Exception:  # noqa: BLE001
            level = 0.0
        try:
            rev = int(self._app.live_rev())
        except Exception:  # noqa: BLE001
            rev = 0
        try:
            notice_rev = int(self._app.notice_rev())
        except Exception:  # noqa: BLE001
            notice_rev = 0
        try:
            model_ok = bool(self._app.model_ok())
        except Exception:  # noqa: BLE001
            model_ok = True
        try:
            model_loaded = bool(self._app.transcriber.is_loaded)
        except Exception:  # noqa: BLE001
            model_loaded = True
        return {
            "state": state,
            "level": level,
            "liveRev": rev,
            "noticeRev": notice_rev,
            "modelOk": model_ok,
            "modelLoaded": model_loaded,
        }

    def get_notice(self) -> dict:
        """Dernière notice utilisateur ({rev, text, kind}) — toast de la fenêtre.

        Récupérée par le JS quand ``poll().noticeRev`` change (jamais en continu).
        """
        try:
            return self._app.notice()
        except Exception:  # noqa: BLE001
            return {"rev": 0, "text": "", "kind": "info"}

    # -- modèle Whisper (état + téléchargement opt-in) --------------------------
    def model_status(self) -> dict:
        """État du modèle pour la bannière du dashboard (absence + téléchargement).

        Renvoie ``{ok, error, size, canDownload, sizeLabel, download:{state, message,
        mb}}`` ; interrogé ponctuellement quand ``poll().modelOk`` est faux, puis par
        polling pendant un téléchargement (comme l'installation GPU).
        """
        try:
            return self._app.model_status()
        except Exception:  # noqa: BLE001
            logger.exception("Lecture de l'état du modèle échouée")
            return {
                "ok": True, "error": "", "size": "", "canDownload": False,
                "sizeLabel": "", "download": {"state": "idle", "message": "", "mb": 0},
            }

    def download_model(self) -> dict:
        """Lance le téléchargement opt-in du modèle manquant. Non bloquant.

        Seule exception réseau du projet (avec l'installation GPU) : déclenchée par un
        clic explicite, suivie par polling ``model_status``. Fonctionne aussi dans
        l'exe figé (huggingface_hub est embarqué).
        """
        try:
            return self._app.start_model_download()
        except Exception:  # noqa: BLE001
            logger.exception("Lancement du téléchargement du modèle échoué")
            return {"ok": False, "error": "Téléchargement impossible (voir logs)."}

    def get_live_text(self) -> dict:
        """Flux live courant ({rev, text}) pour la tuile « Dernière transcription ».

        Affichage progressif des modes live/réunion : chaque segment transcrit s'y ajoute
        au fil de l'eau. Récupéré par le JS quand ``poll().liveRev`` a changé.
        """
        try:
            return self._app.live_transcript()
        except Exception:  # noqa: BLE001
            return {"rev": 0, "text": ""}

    def set_mode(self, mode: str) -> dict:
        if mode in ("dictee", "live", "conference"):
            self._mode = mode
        return {"ok": True}

    def add_note(self, text: Optional[str] = None, stamp: Optional[str] = None) -> dict:
        """Note utilisateur pendant une session live/réunion (UC-16).

        ``stamp`` optionnel = horodatage du segment cité (action « Noter » sur une
        ligne du flux) ; sinon la note est horodatée au moment de la validation.
        """
        try:
            return self._app.add_note(text, stamp)
        except Exception:  # noqa: BLE001
            logger.exception("add_note a échoué")
            return {"ok": False, "error": "Note impossible (voir logs)."}

    def get_speakers(self) -> dict:
        """Locuteurs détectés en réunion diarisée (UC-18) : {active, speakers:[…]}.

        Récupéré par le JS quand ``poll().liveRev`` change et l'état est ``conference``
        (les locuteurs apparaissent/évoluent avec les segments). ``active: false`` hors
        diarisation → le panneau de renommage reste masqué."""
        try:
            return self._app.speakers()
        except Exception:  # noqa: BLE001
            return {"active": False, "speakers": []}

    def rename_speaker(self, key: Optional[str] = None, name: Optional[str] = None) -> dict:
        """Renomme un locuteur détecté (FR-31/US-12), sans interrompre la capture.

        Le renommage est rétroactif (flux en direct, export, historique de la session)."""
        try:
            return self._app.rename_speaker(key, name)
        except Exception:  # noqa: BLE001
            logger.exception("rename_speaker a échoué")
            return {"ok": False, "error": "Renommage impossible (voir logs)."}

    def toggle_record(self) -> dict:
        """Démarre/arrête selon le mode courant et l'état (délègue à WhispertyApp)."""
        from .tray import TrayState

        # Instantané de l'état sous verrou ; les méthodes appelées re-valident l'état
        # sous leur propre verrou (l'instantané ne sert qu'à choisir l'action).
        with self._app._lock:
            state = self._app._state
        try:
            if state is TrayState.IDLE:
                if self._mode == "live":
                    self._app.start_live(self._source)
                elif self._mode == "conference":
                    self._app.start_conference(self._source)
                else:
                    self._app.toggle()
            elif state is TrayState.RECORDING:
                self._app.toggle()
            elif state is TrayState.LIVE:
                self._app.stop_live()
            elif state is TrayState.CONFERENCE:
                self._app.stop_conference()
            # PROCESSING : ignoré (transcription/chargement en cours).
        except Exception:  # noqa: BLE001
            logger.exception("toggle_record a échoué")
        return {"ok": True}

    def get_dashboard(self) -> dict:
        words, dur, trans = self._today_stats()
        try:
            last = self._app.history.last_text() or ""
        except Exception:  # noqa: BLE001
            last = ""
        c = self._app.config
        # Device RÉEL du dernier chargement (repli gracieux CUDA→CPU possible) plutôt que
        # le device configuré : la barre latérale ne doit pas afficher « CUDA » quand la
        # transcription tourne en fait sur le CPU. Repli sur la config tant que le modèle
        # n'a pas été chargé.
        try:
            device = self._app.transcriber.effective_device
        except Exception:  # noqa: BLE001
            device = None
        return {
            "lastText": last,
            "statsWords": words,
            "statsDur": dur,
            "statsTrans": trans,
            "combo": c.hotkey.combo,
            # Taille lisible : un modèle bundlé est un chemin (models/faster-whisper-
            # medium) — la barre latérale doit afficher « whisper-medium », pas le chemin.
            "model": model_size_name(c.transcription.model),
            "device": (device or c.transcription.device or "cpu").upper(),
        }

    def _today_stats(self) -> tuple[int, int, int]:
        """(mots, durée_estimée_min, nb) des transcriptions du jour.

        La base d'historique ne stocke pas la durée audio : la durée est *estimée*
        à partir du nombre de mots (~150 mots/min de parole). L'écran l'indique.
        """
        try:
            # Assez large pour couvrir la journée même si l'utilisateur a relevé la
            # borne de rétention au-delà de 500 entrées.
            limit = max(500, int(self._app.config.history.max_entries or 0))
            entries = self._app.history.recent(limit)
        except Exception:  # noqa: BLE001
            return 0, 0, 0
        today = datetime.now().strftime("%Y-%m-%d")
        todays = [e for e in entries if (e.timestamp or "").startswith(today)]
        words = sum(len((e.text or "").split()) for e in todays)
        return words, round(words / 150), len(todays)

    # -- microphones / configuration ------------------------------------------
    def list_microphones(self) -> list:
        return self._mic_options()

    def _mic_options(self) -> list:
        opts = [{"value": None, "label": "Microphone par défaut"}]
        try:
            from .recorder import list_input_devices

            for d in list_input_devices():
                opts.append({"value": d["index"], "label": f'[{d["index"]}] {d["name"]}'})
        except Exception:  # noqa: BLE001
            logger.exception("Énumération des micros indisponible")
        return opts

    # -- source audio des modes loopback (live/conférence) --------------------
    def list_audio_outputs(self) -> list:
        """Sorties système capturables (loopback) pour les modes live/conférence.

        Renvoie [{value, label}] : None = « Sortie par défaut » puis chaque
        haut-parleur (cf. ``loopback.list_speakers``). Liste réduite au défaut si
        ``soundcard`` est absent — l'UI masque alors le sélecteur (rien à choisir).
        """
        opts = [{"value": None, "label": "Sortie par défaut"}]
        try:
            from .loopback import list_speakers

            for d in list_speakers():
                label = d["name"] + (" (défaut)" if d.get("is_default") else "")
                opts.append({"value": d["index"], "label": label})
        except Exception:  # noqa: BLE001
            logger.exception("Énumération des sorties audio indisponible")
        return opts

    def set_source(self, value) -> dict:
        """Mémorise la source loopback choisie (None/"" = défaut, sinon index)."""
        if value in (None, ""):
            self._source = None
        else:
            try:
                self._source = int(value)
            except (TypeError, ValueError):
                self._source = None
        return {"ok": True}

    def get_config(self) -> dict:
        # CONTRAT : les clés renvoyées ici doivent rester alignées avec celles lues par
        # WhispertyApp.apply_config_from_gui (et le payload construit par web/app.js
        # saveConfig). Toute modification de nom de clé doit être répercutée aux 3 endroits.
        c = self._app.config
        lang = c.transcription.language
        return {
            # Taille normalisée (« medium »), même pour un modèle bundlé en chemin :
            # les boutons de taille de l'écran Configuration raisonnent en tailles, et
            # apply_config_from_gui compare sur la taille (pas d'écrasement d'un
            # modèle local fonctionnel quand on enregistre sans changer de taille).
            "model": model_size_name(c.transcription.model),
            "device": (c.transcription.device or "cpu").upper(),
            # compute_type : pas de champ dédié à l'écran, mais les préréglages de
            # performance le pilotent (« Précis » = float16 si CUDA) et saveConfig
            # le renvoie tel quel — il fait partie du contrat des 3 endroits.
            "compute": (c.transcription.compute_type or "int8").lower(),
            "langue": "auto" if not lang else lang,
            "mic": c.audio.device,
            "mics": self._mic_options(),
            "vad": int(round((c.audio.vad_threshold or 0.0) * 1000)),
            "silence": int(round((c.audio.silence_duration or 0.0) * 1000)),
            "combo": c.hotkey.combo,
            "injection": "frappe" if c.output.method == "type" else "presse",
            "delai": int(round((c.output.type_delay or 0.0) * 1000)),
            "ia": bool(c.ai.enabled),
            "iaEndpoint": c.ai.endpoint,
            "iaModel": c.ai.model,
            "resume": bool(c.summary.enabled),
            "localOnly": bool(c.transcription.local_files_only),
            # Réunion (UC-10 / UC-18) — lu au prochain start_conference().
            "distinguishSpeakers": bool(c.conference.distinguish_speakers),
            "diarization": bool(c.conference.speaker_diarization.enabled),
            "maxSpeakers": int(c.conference.speaker_diarization.max_speakers),
            "labelPrefix": str(c.conference.speaker_diarization.label_prefix or "Locuteur"),
            # Backend d'empreinte vocale (CO-19) : mfcc (défaut) | onnx.
            "diarBackend": str(c.conference.speaker_diarization.backend or "mfcc").lower(),
        }

    def save_config(self, payload: Optional[dict]) -> dict:
        return self._app.apply_config_from_gui(payload or {})

    # -- dictionnaire (édition assistée, UC-19) --------------------------------
    def get_dictionary(self) -> dict:
        """Entrées du dictionnaire pour l'écran « Dictionnaire »."""
        try:
            return self._app.get_dictionary()
        except Exception:  # noqa: BLE001
            logger.exception("Lecture du dictionnaire échouée")
            return {"enabled": True, "hotwords": [], "corrections": []}

    def save_dictionary(self, payload: Optional[dict]) -> dict:
        """Enregistre le dictionnaire édité (écriture + rechargement à chaud)."""
        try:
            return self._app.apply_dictionary_from_gui(payload or {})
        except Exception:  # noqa: BLE001
            logger.exception("Enregistrement du dictionnaire échoué")
            return {"ok": False, "error": "Enregistrement impossible (voir logs)."}

    def open_dictionary(self) -> dict:
        """Ouvre ``dictionary.txt`` dans l'éditeur système (repli)."""
        try:
            self._app.open_dictionary()
        except Exception:  # noqa: BLE001
            logger.exception("Ouverture du dictionnaire échouée")
        return {"ok": True}

    # -- support GPU (CUDA) ----------------------------------------------------
    def gpu_status(self) -> dict:
        """État du support GPU pour l'écran Configuration (détection + installation).

        Renvoie ``{gpu, components, canInstall, install, message}`` : présence d'un GPU
        NVIDIA, présence des composants cuBLAS/cuDNN, possibilité d'installer (mode source),
        et l'état/message de l'installation en cours (interrogé par polling pendant celle-ci).
        """
        try:
            from . import cuda

            return cuda.status()
        except Exception:  # noqa: BLE001
            logger.exception("Lecture de l'état GPU échouée")
            return {"gpu": False, "components": False, "canInstall": False,
                    "install": "idle", "message": ""}

    # -- modèle de diarisation ONNX (CO-19) -------------------------------------
    def diar_model_status(self) -> dict:
        """État du backend de diarisation : {backend, installed, sizeLabel, download}."""
        try:
            return self._app.diar_model_status()
        except Exception:  # noqa: BLE001
            logger.exception("Lecture de l'état du modèle de diarisation échouée")
            return {"backend": "mfcc", "installed": False, "sizeLabel": "",
                    "download": {"state": "idle", "message": "", "mb": 0}}

    def download_diar_model(self) -> dict:
        """Lance le téléchargement **opt-in** du modèle de diarisation (~26 Mo).

        Explicitement déclenché par l'utilisateur, comme le modèle Whisper et les
        composants GPU ; ensuite tout reste hors-ligne. Suivi par ``diar_model_status``."""
        try:
            return self._app.start_diar_model_download()
        except Exception:  # noqa: BLE001
            logger.exception("Lancement du téléchargement de diarisation échoué")
            return {"ok": False, "error": "Téléchargement impossible (voir logs)."}

    # -- bench local (préréglages de performance) -------------------------------
    def run_bench(self) -> dict:
        """Lance le bench local (« Tester sur ce poste »). Non bloquant, zéro réseau.

        L'audio témoin est GÉNÉRÉ localement (transcriber.bench_audio) ; la mesure
        passe par la machine à états (mode exclusif, comme l'import audio) et l'UI
        suit la progression par polling ``bench_status`` (cf. gpu_status)."""
        try:
            return self._app.start_bench()
        except Exception:  # noqa: BLE001
            logger.exception("Lancement du bench échoué")
            return {"ok": False, "error": "Mesure impossible (voir logs)."}

    def bench_status(self) -> dict:
        """État du bench local : {state: idle|running|done|error, seconds, load, message}."""
        try:
            return self._app.bench_status()
        except Exception:  # noqa: BLE001
            return {"state": "error", "seconds": None, "load": None,
                    "message": "État du bench illisible (voir logs)."}

    def install_gpu(self) -> dict:
        """Lance l'installation opt-in des composants GPU (~1,3 Go). Non bloquant.

        Le téléchargement est le SEUL appel réseau, explicitement déclenché par l'utilisateur
        (analogue au téléchargement initial du modèle). La progression est suivie par
        ``gpu_status`` (polling). Indisponible dans l'exe figé (pas de pip).
        """
        try:
            from . import cuda

            return cuda.start_install()
        except Exception:  # noqa: BLE001
            logger.exception("Lancement de l'installation GPU échoué")
            return {"ok": False, "error": "Installation impossible."}

    # -- historique ------------------------------------------------------------
    @staticmethod
    def _payload_speakers(payload: Optional[dict]) -> list:
        """Locuteurs d'une réunion archivée pour l'UI (FR-31), [] sans diarisation.

        Payload minimal : les SEGMENTS (potentiellement volumineux) restent côté
        Python — le JS n'a besoin que du registre pour afficher le renommage.
        """
        if not isinstance(payload, dict):
            return []
        rows = []
        for spk in payload.get("speakers") or []:
            if isinstance(spk, dict) and spk.get("key"):
                auto = str(spk.get("auto") or spk["key"])
                name = str(spk.get("name") or "")
                rows.append({"key": str(spk["key"]), "auto": auto, "name": name,
                             "label": name or auto})
        return rows

    def get_history(self) -> dict:
        try:
            limit = self._app.config.history.max_entries or 200
            entries = self._app.history.recent(limit)
        except Exception:  # noqa: BLE001
            entries = []
        items = [
            {
                "id": str(e.id),
                "time": _fmt_time(e.timestamp),
                "words": len((e.text or "").split()),
                "source": e.source,
                "text": e.text,
                "sec": None,  # durée non stockée (cf. _today_stats)
                # Réunion diarisée : locuteurs renommables après la session (FR-31).
                "speakers": self._payload_speakers(e.payload),
            }
            for e in entries
        ]
        return {"total": len(items), "items": items}

    def rename_history_speaker(
        self, entry_id: Optional[str] = None, key: Optional[str] = None,
        name: Optional[str] = None,
    ) -> dict:
        """Renomme un locuteur d'une réunion archivée (FR-31, post-session).

        Rétroactif à froid : entrée d'historique re-rendue (recherche FTS à jour) et
        fichier exporté réécrit s'il existe encore."""
        try:
            return self._app.rename_history_speaker(entry_id, key, name)
        except Exception:  # noqa: BLE001
            logger.exception("rename_history_speaker a échoué")
            return {"ok": False, "error": "Renommage impossible (voir logs)."}

    def search_history(self, query) -> dict:
        """Recherche plein texte (FTS5, repli LIKE) — renvoie les ids correspondants.

        Payload minimal : le JS possède déjà les entrées complètes (get_history) et
        n'a besoin que de l'ensemble des ids à retenir. ``ids: null`` = recherche
        indisponible (le JS retombe sur son filtre sous-chaîne local).
        """
        try:
            limit = self._app.config.history.max_entries or 200
            entries = self._app.history.search(str(query or ""), limit=limit)
            return {"ids": [str(e.id) for e in entries]}
        except Exception:  # noqa: BLE001
            logger.exception("Recherche dans l'historique échouée")
            return {"ids": None}

    def delete_history(self, entry_id) -> dict:
        try:
            self._app.history.delete(int(entry_id))
        except Exception:  # noqa: BLE001
            logger.exception("Suppression d'une entrée d'historique échouée")
        return {"ok": True}

    def clear_history(self) -> dict:
        try:
            self._app.history.clear()
        except Exception:  # noqa: BLE001
            logger.exception("Purge de l'historique échouée")
        return {"ok": True}

    def copy_text(self, text: Optional[str]) -> dict:
        ok = False
        try:
            ok = bool(self._app.injector.copy_to_clipboard(text or ""))
        except Exception:  # noqa: BLE001
            logger.exception("Copie dans le presse-papiers échouée")
        return {"ok": ok}

    # -- utilitaire ------------------------------------------------------------
    def _safe(self, fn) -> dict:
        try:
            if self._window is not None:
                fn()
        except Exception:  # noqa: BLE001
            logger.exception("Action fenêtre échouée")
        return {"ok": True}


def _harden_webview_env() -> None:
    """Réduit le trafic de fond du runtime WebView2 (Edge, base Chromium).

    La page est 100 % locale : le navigateur n'a aucune raison légitime de parler
    au réseau. Ces arguments coupent les canaux de fond connus (réseau d'arrière-
    plan, rapports de fiabilité de domaine, mise à jour de composants). Best-effort :
    un argument inconnu est ignoré par Chromium, et le trafic résiduel imputable au
    composant OS (Edge/WebView2 lui-même) ne dépend pas de Whisperty. On COMPLÈTE
    un réglage utilisateur existant sans l'écraser.
    """
    var = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
    wanted = (
        "--disable-background-networking",
        "--disable-domain-reliability",
        "--disable-component-update",
    )
    existing = os.environ.get(var, "")
    merged = existing.split() if existing else []
    merged.extend(arg for arg in wanted if arg not in merged)
    os.environ[var] = " ".join(merged)


def launch_gui(app: "WhispertyApp") -> None:
    """Crée la fenêtre et tient le thread principal jusqu'à sa destruction.

    Doit être appelée sur le thread principal (exigence des GUI Windows). Lève si
    ``pywebview`` ou WebView2 est indisponible : l'appelant (``run``) gère le repli.
    """
    _harden_webview_env()  # AVANT la création du runtime WebView2
    import webview  # import paresseux : dépendance optionnelle

    api = GuiApi(app)
    index = web_dir() / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"Assets web introuvables : {index}")

    window = webview.create_window(
        "Whisperty",
        url=str(index),
        js_api=api,
        width=1100,
        height=740,
        min_size=(800, 580),
        frameless=True,
        resizable=True,
        easy_drag=False,
        background_color="#09090f",
    )
    api._window = window  # privé : non exposé à l'introspection js_api de pywebview
    # Publication de _gui sous verrou : le thread tray (show_window) le lit aussi.
    with app._lock:
        app._gui = api  # permet au tray (« Ouvrir Whisperty ») de ré-afficher la fenêtre

    def on_closing():
        # Quitte réellement si l'app est en cours d'arrêt ; sinon masque dans le tray.
        if getattr(app, "_quitting", False):
            return True
        try:
            window.hide()
            api.notify_hidden_once()
        except Exception:  # noqa: BLE001
            logger.exception("Masquage de la fenêtre échoué")
        return False

    try:
        window.events.closing += on_closing
    except Exception:  # noqa: BLE001
        logger.debug("events.closing indisponible sur ce backend.")

    logger.info("Interface fenêtre Whisperty (WebView2).")
    webview.start(gui="edgechromium", debug=False)
