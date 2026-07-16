from flask import render_template
from flask_login import login_required

from app.aide import bp
from app.aide.contenu import NOTICE


@bp.route("/")
@login_required
def notice():
    """Centre d'aide : la notice complète de l'application, imprimable."""
    return render_template("aide/notice.html", chapitres=NOTICE)
