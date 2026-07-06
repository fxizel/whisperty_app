"""Métadonnées légales et URL publiques (installeur, signature, UI)."""

from __future__ import annotations

# Dépôt public — source unique pour les URL affichées dans l'installeur et l'UI.
REPOSITORY_URL = "https://github.com/fxizel/whisperty_app"

# Politique de confidentialité (hébergée sur GitHub ; à recopier dans les portails
# de signature de code, Microsoft Store, etc.).
PRIVACY_POLICY_URL = f"{REPOSITORY_URL}/blob/main/docs/privacy-policy.md"

PUBLISHER = "fxizel"  # Projet personnel open source (dépôt GitHub fxizel/whisperty_app).


def legal_info() -> dict[str, str]:
    """Métadonnées exposées au pont GUI (polling via get_version)."""
    return {
        "publisher": PUBLISHER,
        "repositoryUrl": REPOSITORY_URL,
        "privacyPolicyUrl": PRIVACY_POLICY_URL,
    }
