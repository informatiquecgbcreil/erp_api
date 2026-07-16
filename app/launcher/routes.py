from __future__ import annotations

import io

from flask import render_template, request, Response, url_for
from werkzeug.routing import BuildError

import segno

from app.services.public_urls import public_base_url

from . import bp


@bp.get("/")
def index():
    base = public_base_url()

    # Kiosque : dans ton app l'endpoint s'appelle kiosk.kiosk_home
    try:
        kiosk_path = url_for("kiosk.kiosk_home")
    except BuildError:
        # fallback si jamais tu changes encore le nom
        kiosk_path = "/kiosk/"

    kiosk_url = f"{base}{kiosk_path}"

    # Admin : on envoie sur le login (l'admin reste protégé)
    try:
        admin_path = url_for("auth.login")
    except BuildError:
        admin_path = "/auth/login"

    admin_url = f"{base}{admin_path}"

    return render_template("launcher/index.html", kiosk_url=kiosk_url, admin_url=admin_url)


@bp.get("/qr")
def launcher_qr():
    """Retourne un QR code SVG pour une URL donnée (par défaut: kiosque).

    /launcher/qr?target=kiosk
    /launcher/qr?target=admin
    /launcher/qr?u=https://...
    """
    base = public_base_url()
    target = (request.args.get("target") or "kiosk").strip().lower()
    u = (request.args.get("u") or "").strip()

    if not u:
        if target == "admin":
            try:
                u = f"{base}{url_for('auth.login')}"
            except BuildError:
                u = f"{base}/auth/login"
        else:
            try:
                u = f"{base}{url_for('kiosk.kiosk_home')}"
            except BuildError:
                u = f"{base}/kiosk/"

    qr = segno.make(u, error="M")

    # segno peut écrire des bytes -> BytesIO obligatoire
    buff = io.BytesIO()
    qr.save(buff, kind="svg", xmldecl=False, svgclass="qr")
    svg_bytes = buff.getvalue()

    return Response(svg_bytes, mimetype="image/svg+xml")
