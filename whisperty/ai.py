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

        endpoint = self.cfg.endpoint
        if not is_local_endpoint(endpoint):
            # Garde de confidentialité : on refuse d'exfiltrer le texte dicté.
            logger.error(
                "Mode IA ignoré : l'endpoint '%s' n'est pas local. "
                "La confidentialité interdit tout envoi hors de la machine.",
                endpoint,
            )
            return text

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": self.cfg.prompt},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "temperature": 0,
        }
        try:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # _OPENER refuse les redirections (cf. _NoRedirectHandler) : pas de re-POST
            # silencieux vers un hôte distant.
            with _OPENER.open(request, timeout=self.cfg.timeout) as response:
                # Défense en profondeur : l'URL effective doit rester locale.
                final_url = response.geturl()
                if not is_local_endpoint(final_url):
                    logger.error(
                        "Réponse IA via une URL non locale (%s) ; ignorée.", final_url
                    )
                    return text
                data = json.loads(response.read().decode("utf-8"))
            refined = data["choices"][0]["message"]["content"].strip()
            if refined:
                logger.info("Texte raffiné par le LLM local '%s'.", self.cfg.model)
                return refined
            logger.warning("Réponse IA vide ; texte brut conservé.")
        except (urllib.error.URLError, OSError) as exc:
            logger.warning(
                "LLM local injoignable (%s) ; texte brut conservé. "
                "Vérifiez que le serveur (ex. Ollama) tourne sur %s.",
                exc, endpoint,
            )
        except (KeyError, ValueError, json.JSONDecodeError):
            logger.warning("Réponse IA inattendue ; texte brut conservé.", exc_info=True)
        return text
