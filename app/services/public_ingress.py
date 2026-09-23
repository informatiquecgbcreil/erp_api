"""Identification restrictive de la façade publique.

Tailscale supprime l'en-tête reçu du visiteur puis le réécrit pour Funnel.
L'ajout de cet en-tête ne confère jamais de privilège : il les réduit.
Le port public du proxy Windows reste filtré indépendamment de ces contrôles.
"""
from urllib.parse import urlsplit
from flask import current_app, request


def hostname(value):
    try:
        return (urlsplit(value if "://" in value else "//" + value).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def is_public_ingress():
    if "Tailscale-Funnel-Request" in request.headers:
        return True
    public = hostname(current_app.config.get("KIOSK_PUBLIC_HOST") or "")
    if not public:
        return False
    incoming = hostname(request.host or "")
    if incoming == public or not incoming:
        return True
    private = {hostname(current_app.config.get("PUBLIC_BASE_URL") or ""), "localhost", "127.0.0.1", "::1"}
    private.update(hostname(h) for h in current_app.config.get("TRUSTED_HOSTS") or [])
    # Une adresse inconnue ne transforme pas une requête en accès interne.
    return incoming not in private
