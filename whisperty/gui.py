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
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

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
        self._maximized = False

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
        return self._safe(lambda: self._window.hide())


    def win_move(self, x: int, y: int) -> dict:
        """Déplace la fenêtre à la position absolue (x, y) en pixels écran."""
        return self._safe(lambda: self._window.move(int(x), int(y)))

    # -- état / dashboard ------------------------------------------------------
    def poll(self) -> dict:
        """État courant + niveau RMS (appelé ~5×/s par le JS pour le visualiseur)."""
        try:
            with self._app._lock:
                state = self._app._state.value
        except Exception:  # noqa: BLE001
            state = "idle"
        try:
            level = float(self._app.recorder.current_level)
        except Exception:  # noqa: BLE001
            level = 0.0
        return {"state": state, "level": level}

    def set_mode(self, mode: str) -> dict:
        if mode in ("dictee", "live", "conference"):
            self._mode = mode
        return {"ok": True}

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
                    self._app.start_live()
                elif self._mode == "conference":
                    self._app.start_conference()
                else:
                    self._app.toggle()
            elif state is TrayState.RECORDING:
                self._app.toggle()
            elif state is TrayState.LIVE:
                self._app.stop_live()
            elif state is TrayState.CONFERENCE:
                self._app.stop_conference()
            elif state is TrayState.MEETING:
                self._app.stop_meeting()
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
        return {
            "lastText": last,
            "statsWords": words,
            "statsDur": dur,
            "statsTrans": trans,
            "combo": c.hotkey.combo,
            "model": c.transcription.model,
            "device": (c.transcription.device or "cpu").upper(),
        }

    def _today_stats(self) -> tuple[int, int, int]:
        """(mots, durée_estimée_min, nb) des transcriptions du jour.

        La base d'historique ne stocke pas la durée audio : la durée est *estimée*
        à partir du nombre de mots (~150 mots/min de parole). L'écran l'indique.
        """
        try:
            entries = self._app.history.recent(500)
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

    def get_config(self) -> dict:
        # CONTRAT : les clés renvoyées ici doivent rester alignées avec celles lues par
        # WhispertyApp.apply_config_from_gui (et le payload construit par web/app.js
        # saveConfig). Toute modification de nom de clé doit être répercutée aux 3 endroits.
        c = self._app.config
        lang = c.transcription.language
        return {
            "model": c.transcription.model,
            "device": (c.transcription.device or "cpu").upper(),
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
            "localOnly": bool(c.transcription.local_files_only),
        }

    def save_config(self, payload: Optional[dict]) -> dict:
        return self._app.apply_config_from_gui(payload or {})

    # -- historique ------------------------------------------------------------
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
            }
            for e in entries
        ]
        return {"total": len(items), "items": items}

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


def launch_gui(app: "WhispertyApp") -> None:
    """Crée la fenêtre et tient le thread principal jusqu'à sa destruction.

    Doit être appelée sur le thread principal (exigence des GUI Windows). Lève si
    ``pywebview`` ou WebView2 est indisponible : l'appelant (``run``) gère le repli.
    """
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
        except Exception:  # noqa: BLE001
            logger.exception("Masquage de la fenêtre échoué")
        return False

    try:
        window.events.closing += on_closing
    except Exception:  # noqa: BLE001
        logger.debug("events.closing indisponible sur ce backend.")

    logger.info("Interface fenêtre Whisperty (WebView2).")
    webview.start(gui="edgechromium", debug=False)
