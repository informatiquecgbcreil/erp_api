"""Aucun formulaire imbriqué dans les gabarits.

Imbriquer deux <form> est interdit en HTML : le navigateur referme
silencieusement le parent, et tout ce qui suit — dont son bouton
d'enregistrement — cesse d'appartenir au formulaire. Le bouton semble alors
« ne pas fonctionner » alors qu'il ne soumet simplement plus rien.

C'est exactement ce qui est arrivé à la fiche participant : les formulaires
d'anonymisation, affichés en édition seulement, coupaient le formulaire juste
avant « Enregistrer les modifications » — d'où un bug visible en modification
mais pas en création. Ce test empêche la situation de revenir, sur toutes les
pages à la fois.
"""
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent / "app" / "templates"

#: Commentaires Jinja et HTML, et blocs <script> : le mot « form » y apparaît
#: sans être une balise (plusieurs gabarits documentent justement la règle).
BRUIT = [
    re.compile(r"\{#.*?#\}", re.S),
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"<script\b.*?</script>", re.S | re.I),
]


def _profondeur_max(source: str) -> tuple[int, int]:
    """(profondeur maximale, ligne où elle est atteinte)."""
    for motif in BRUIT:
        source = motif.sub(lambda m: "\n" * m.group().count("\n"), source)

    profondeur = maxi = ligne_maxi = 0
    for balise in re.finditer(r"<form\b|</form\s*>", source, re.I):
        if balise.group().lower().startswith("<form"):
            profondeur += 1
            if profondeur > maxi:
                maxi = profondeur
                ligne_maxi = source.count("\n", 0, balise.start()) + 1
        else:
            profondeur -= 1
    return maxi, ligne_maxi


def test_aucun_formulaire_imbrique():
    fautifs = []
    for gabarit in sorted(RACINE.rglob("*.html")):
        profondeur, ligne = _profondeur_max(gabarit.read_text(encoding="utf-8"))
        if profondeur > 1:
            fautifs.append(f"{gabarit.relative_to(RACINE)} (ligne {ligne}, profondeur {profondeur})")
    assert not fautifs, "formulaires imbriqués : " + " ; ".join(fautifs)


def test_le_detecteur_repere_bien_une_imbrication():
    """Garde-fou du garde-fou : sans lui, le test précédent pourrait passer
    parce qu'il ne détecte rien, et non parce que tout est sain."""
    assert _profondeur_max("<form><form></form></form>")[0] == 2
    assert _profondeur_max("<form></form><form></form>")[0] == 1
    # Un commentaire qui parle de formulaires ne doit pas compter.
    assert _profondeur_max("{# ne pas imbriquer <form> dans <form> #}<form></form>")[0] == 1
