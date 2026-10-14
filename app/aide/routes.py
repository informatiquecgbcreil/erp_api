import re

from flask import render_template
from flask_login import login_required
from markupsafe import Markup, escape

from app.aide import bp
from app.aide.contenu import FICHE_REFLEXE, NOTICE


@bp.route("/")
@login_required
def notice():
    """Centre d'aide : la notice complète de l'application, imprimable."""
    return render_template("aide/notice.html", chapitres=NOTICE)


@bp.route("/fiche-reflexe")
@login_required
def fiche_reflexe():
    """Une page A4 à imprimer et coller près du poste : « il se passe ceci → je fais cela »."""
    from app.pwa.routes import adresse_installation

    situations = [{**cas, "geste_html": _boutons(cas["geste"])} for cas in FICHE_REFLEXE]
    return render_template("aide/fiche_reflexe.html", situations=situations, adresse=adresse_installation())


def _boutons(texte: str) -> Markup:
    """Les libellés « … » en gras : sur papier, l'œil cherche le nom du bouton."""
    return Markup(re.sub(r"«\s*([^»]+?)\s*»", r'<span class="bouton">\1</span>', str(escape(texte))))
