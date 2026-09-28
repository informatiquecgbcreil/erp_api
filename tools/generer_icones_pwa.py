"""Icônes de l'appli installable (app/static/pwa), dessinées comme l'icône de
l'installateur Windows (desktop/build.py). À relancer si le pictogramme change :

    python tools/generer_icones_pwa.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

SORTIE = Path(__file__).resolve().parents[1] / "app" / "static" / "pwa"
TEAL, OR = "#135b63", "#f2b84b"


def pictogramme(taille: int, *, plein: bool, marge: float = 0.0) -> Image.Image:
    """plein : fond carré sans coins arrondis (icône « maskable », que le
    système découpe lui-même) ; marge : part réservée autour du dessin."""
    im = Image.new("RGBA", (1024, 1024))
    d = ImageDraw.Draw(im)
    if plein:
        d.rectangle((0, 0, 1023, 1023), fill=TEAL)
    else:
        d.rounded_rectangle((0, 0, 1023, 1023), radius=224, fill=TEAL)
    echelle = 4 * (1 - 2 * marge)  # 256 → 1024 px
    decalage = 1024 * marge

    def p(x, y):
        return (decalage + x * echelle, decalage + y * echelle)

    def pts(valeurs):
        return [p(x, y) for x, y in valeurs]

    def boite(x0, y0, x1, y1):
        return (*p(x0, y0), *p(x1, y1))

    # Tracé de app/static/mon-centre-social.svg (repère 256 × 256).
    d.polygon(pts([(40, 121), (128, 47), (216, 121), (216, 204), (40, 204)]), fill="white")
    trait = 16 * echelle
    d.line(pts([(34, 124), (128, 44), (222, 124)]), fill=OR, width=round(trait), joint="curve")
    for x, y in ((34, 124), (222, 124)):  # extrémités arrondies du toit
        d.ellipse(boite(x - 8, y - 8, x + 8, y + 8), fill=OR)

    def silhouette(cx, haut, bas, rayon, couleur):
        # Demi-disque de rayon « rayon » centré (cx, haut), prolongé jusqu'à « bas ».
        d.pieslice(boite(cx - rayon, haut - rayon, cx + rayon, haut + rayon), 180, 360, fill=couleur)
        d.rectangle(boite(cx - rayon, haut, cx + rayon, bas), fill=couleur)

    for cx in (98, 158):
        d.ellipse(boite(cx - 16, 111, cx + 16, 143), fill=TEAL)
        silhouette(cx, 169, 192, 28, TEAL)
    silhouette(128, 173, 190, 19, OR)
    d.ellipse(boite(116, 139, 140, 163), fill=OR)
    return im.resize((taille, taille), Image.LANCZOS)


def main():
    SORTIE.mkdir(parents=True, exist_ok=True)
    pictogramme(192, plein=False).save(SORTIE / "icone-192.png", optimize=True)
    pictogramme(512, plein=False).save(SORTIE / "icone-512.png", optimize=True)
    # Zone sûre des icônes maskable : cercle de 80 % du côté.
    pictogramme(512, plein=True, marge=0.12).save(SORTIE / "icone-maskable-512.png", optimize=True)
    # iOS arrondit lui-même et ne gère pas la transparence : fond plein.
    pictogramme(180, plein=True, marge=0.06).convert("RGB").save(SORTIE / "apple-touch-icon.png", optimize=True)


if __name__ == "__main__":
    main()
