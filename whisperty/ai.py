"""Whisperty — raffinage optionnel du texte par un LLM **local** (V2).

Post-traitement facultatif : envoie le texte transcrit à un modèle de langage
tournant **sur la machine** (Ollama, LM Studio, llama.cpp server…) exposant une
API compatible OpenAI (``/v1/chat/completions``). Utile pour reponctuer, retirer
les hésitations ou corriger la casse.

Confidentialité (contrainte cardinale) : le texte ne doit JAMAIS quitter la
machine. Une garde refuse tout endpoint non-local (seuls ``localhost``,
``127.0.0.1`` et ``::1`` sont acceptés). Le mode est **désactivé par défaut** et
n'introduit aucune dépendance (``urllib`` standard). En cas d'échec (serveur
absent, délai dépassé), le texte brut est conservé : jamais bloquant.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .config import AIConfig

logger = logging.getLogger(__name__)

# Hôtes considérés comme strictement locaux (aucune donnée ne sort de la machine).
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", ""}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Bloque tout suivi de redirection 3xx.

    Sans cette garde, ``urllib`` réémet la requête POST (donc le texte dicté) vers
    la cible d'un ``Location`` — un serveur local compromis ou un proxy mal réglé
    pourrait ainsi exfiltrer le texte vers un hôte distant en répondant 302/307,
    sans jamais modifier l'``endpoint`` configuré. Refuser la redirection la
    transforme en ``HTTPError`` (capturée comme un échec) : le texte brut est conservé.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Opener dédié : handlers par défaut, mais sans suivi de redirection.
_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def is_local_endpoint(endpoint: str) -> bool:
    """Vrai si l'URL pointe vers la machine locale (http/https + hôte local)."""
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _LOCAL_HOSTS


class LocalLLM:
    """Raffineur de texte via un LLM local compatible OpenAI."""

    def __init__(self, cfg: "AIConfig") -> None:
        self.cfg = cfg

    def refine(self, text: str) -> str:
        """Renvoie le texte raffiné par le LLM local, ou le texte d'origine en repli.

        Ne lève jamais : toute erreur (désactivé, endpoint distant, serveur muet)
        retourne le texte initial pour ne pas casser le pipeline de dictée.
        """
        if not self.cfg.enabled or not text or not text.strip():
            return text
        result = self._chat(self.cfg.prompt, text)
        return result if result else text

    def _chat(self, system: str, user: str) -> str | None:
        """Appel générique au LLM local. Renvoie None en cas d'échec (jamais d'exception)."""
        if not self.cfg.enabled:
            return None

        endpoint = self.cfg.endpoint
        if not is_local_endpoint(endpoint):
            logger.error(
                "Mode IA ignoré : l'endpoint '%s' n'est pas local. "
                "La confidentialité interdit tout envoi hors de la machine.",
                endpoint,
            )
            return None

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": 0.3,
        }
        try:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _OPENER.open(request, timeout=self.cfg.timeout) as response:
                final_url = response.geturl()
                if not is_local_endpoint(final_url):
                    logger.error(
                        "Réponse IA via une URL non locale (%s) ; ignorée.", final_url
                    )
                    return None
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            if content:
                return content
            logger.warning("Réponse IA vide.")
        except (urllib.error.URLError, OSError) as exc:
            logger.warning(
                "LLM local injoignable (%s) ; vérifiez que le serveur tourne sur %s.",
                exc, endpoint,
            )
        # IndexError : "choices" vide ; TypeError/AttributeError : nœud non-dict (serveur
        # local non conforme). Contrat de la méthode : ne JAMAIS lever (texte brut conservé).
        except (KeyError, IndexError, TypeError, AttributeError, ValueError, json.JSONDecodeError):
            logger.warning("Réponse IA inattendue.", exc_info=True)
        return None
