from __future__ import annotations

import json

from flask import Response, current_app, render_template, url_for
from flask_login import login_required

from app.services.public_urls import public_base_url

from . import bp

#: Changer ce numéro force les appareils à reprendre le service worker.
VERSION_SW = "1"


def _identite():
    from app.services.instance_settings import resolve_identity
    nom_app, structure, _, _ = resolve_identity(current_app.config.get("APP_NAME", "Mon Centre Social"),
                                                current_app.config.get("ORGANIZATION_NAME", ""))
    return nom_app, structure


def adresse_installation() -> str:
    """Adresse que le QR code et l'affiche donnent aux collègues."""
    return f"{public_base_url()}{url_for('pwa.installer')}"


@bp.get("/manifest.webmanifest")
def manifeste():
    nom_app, structure = _identite()
    icone = "pwa/icone-{}.png"
    manifeste = {
        "id": "/",
        "name": f"{nom_app} — {structure}" if structure and structure != nom_app else nom_app,
        "short_name": nom_app[:24],
        "description": "Application de gestion du centre, sur le réseau du centre.",
        "lang": "fr",
        "start_url": url_for("auth.login"),
        "scope": "/",
        "display": "standalone",
        "background_color": "#135b63",
        "theme_color": "#135b63",
        "icons": [
            {"src": url_for("static", filename=icone.format(192)), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename=icone.format(512)), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="pwa/icone-maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    reponse = Response(json.dumps(manifeste, ensure_ascii=False), mimetype="application/manifest+json")
    reponse.headers["Cache-Control"] = "no-cache"
    return reponse


@bp.get("/sw.js")
def service_worker():
    corps = render_template("pwa/sw.js", version=VERSION_SW, hors_ligne=url_for("pwa.hors_ligne"),
                            icone=url_for("static", filename="pwa/icone-192.png"))
    reponse = Response(corps, mimetype="text/javascript")
    # Toujours revérifié : une mise à jour de l'application est prise tout de suite.
    reponse.headers["Cache-Control"] = "no-cache"
    reponse.headers["Service-Worker-Allowed"] = "/"
    return reponse


@bp.get("/hors-ligne")
def hors_ligne():
    nom_app, structure = _identite()
    return render_template("pwa/hors_ligne.html", nom_app=nom_app, structure=structure)


@bp.get("/installer")
def installer():
    nom_app, structure = _identite()
    return render_template("pwa/installer.html", nom_app=nom_app, structure=structure,
                           adresse=adresse_installation(), base=public_base_url())


@bp.get("/installer/affiche")
@login_required
def affiche():
    nom_app, structure = _identite()
    return render_template("pwa/affiche.html", nom_app=nom_app, structure=structure,
                           adresse=adresse_installation())
