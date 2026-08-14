"""Génère l'icône de l'application (installer/whisperty.ico).

Identité visuelle de référence (ce fichier fait foi depuis le retrait de la planche de
maquettage, qui référençait un CDN de polices) : une **onde sonore** symétrique (7 barres
blanches arrondies) dans un squircle style Windows 11 au dégradé violet diagonal
(#7c3aed → #a855f7). 100 % local (Pillow), aucun asset distant.
Lancer une fois ; le .ico produit est versionné et réutilisé par le .spec PyInstaller
et l'installeur Inno Setup.

    python scripts/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Rendu à haute résolution puis sous-échantillonné (anti-crénelage), enfin empaqueté
# en .ico multi-tailles (Windows choisit la taille adaptée à chaque contexte).
_SS = 8                      # facteur de suréchantillonnage
_BASE = 256                  # taille logique de référence
_SIZES = [256, 128, 64, 48, 32, 24, 16]

_VIOLET_TOP = (124, 58, 237)     # #7c3aed (haut-gauche du dégradé)
_VIOLET_BOTTOM = (168, 85, 247)  # #a855f7 (bas-droite du dégradé)
_WHITE = (255, 255, 255, 255)

# Onde sonore : 7 barres verticales symétriques (coordonnées de la maquette, viewBox 100).
# (x gauche, hauteur, opacité) — largeur 6, rayon 3, toutes centrées sur y=50.
_BAR_W = 6
_BARS = [
    (17, 28, 0.92),
    (27, 46, 0.92),
    (37, 64, 1.0),
    (47, 80, 1.0),
    (57, 64, 1.0),
    (67, 46, 0.92),
    (77, 28, 0.92),
]


def _gradient(size: int) -> Image.Image:
    """Dégradé violet diagonal (haut-gauche → bas-droite), comme la maquette.

    Le dégradé est linéaire (donc lisse) : on le calcule à basse résolution puis on
    l'agrandit — bien plus rapide qu'une double boucle sur ``size×size`` pixels.
    """
    n = 256
    small = Image.new("RGB", (n, n))
    px = small.load()
    denom = 2 * (n - 1)
    for y in range(n):
        for x in range(n):
            t = (x + y) / denom
            px[x, y] = tuple(
                int(a + (b - a) * t) for a, b in zip(_VIOLET_TOP, _VIOLET_BOTTOM)
            )
    return small.resize((size, size), Image.BILINEAR)


def _render(px: int) -> Image.Image:
    """Dessine l'icône à la taille ``px`` (suréchantillonnée)."""
    s = px * _SS
    sc = s / 100.0  # échelle viewBox(100) → pixels
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Fond : squircle (coins arrondis) avec dégradé violet diagonal.
    margin = 2 * sc
    radius = int(26 * sc)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, s - 1 - margin, s - 1 - margin), radius=radius, fill=255
    )
    img.paste(_gradient(s), (0, 0), mask)

    # Onde sonore : barres blanches. Les barres externes sont à 0.92 d'opacité ; on
    # dessine chaque groupe d'opacité sur un calque dédié puis on le compose (l'alpha
    # d'un fill ImageDraw ne se composite pas tout seul sur du RGBA).
    for opacity in (1.0, 0.92):
        bars = [b for b in _BARS if b[2] == opacity]
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for x, h, _ in bars:
            top, bottom = (50 - h / 2) * sc, (50 + h / 2) * sc
            ld.rounded_rectangle(
                (x * sc, top, (x + _BAR_W) * sc, bottom), radius=3 * sc, fill=_WHITE
            )
        if opacity < 1.0:
            layer.putalpha(layer.split()[3].point(lambda a: int(a * opacity)))
        img.alpha_composite(layer)

    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "installer" / "whisperty.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    base = _render(_BASE)
    frames = [base.resize((sz, sz), Image.LANCZOS) for sz in _SIZES]
    frames[0].save(out, format="ICO", sizes=[(sz, sz) for sz in _SIZES],
                   append_images=frames[1:])
    print(f"Icône écrite : {out}")


if __name__ == "__main__":
    main()
