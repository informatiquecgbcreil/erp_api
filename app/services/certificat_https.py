"""Alerte d'expiration du certificat reconnu (nom public, Let's Encrypt).

Le renouvellement tourne sur le serveur Windows (tâche planifiée) ; il écrit
son état, sans aucun secret, dans runtime/certificat-public.json. S'il échoue
en silence, tous les appareils afficheraient une alerte de sécurité à
l'échéance : les administrateurs sont prévenus bien avant.
"""
from __future__ import annotations

import datetime
import json

from app.services.registre_externe import dossier_runtime

#: Avant l'échéance : alerte si le dernier renouvellement a échoué…
SEUIL_ECHEC = datetime.timedelta(days=21)
#: … et dans tous les cas (renouvellement censé avoir eu lieu à J-30).
SEUIL_ABSOLU = datetime.timedelta(days=14)


def alerte_certificat(maintenant: datetime.datetime | None = None) -> str | None:
    dossier = dossier_runtime()
    if dossier is None:
        return None
    try:
        etat = json.loads((dossier / "certificat-public.json").read_text(encoding="utf-8"))
        expire = datetime.datetime.fromisoformat(etat["expire_le"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    maintenant = maintenant or datetime.datetime.now(datetime.timezone.utc)
    reste = expire - maintenant
    nom = etat.get("nom") or "le nom public"
    erreur = etat.get("erreur")
    conseil = ("Sur le serveur : menu Démarrer → « Certificat reconnu (nom public) », "
               "journal logs\\certificat.log.")
    if reste <= datetime.timedelta(0):
        return f"Le certificat HTTPS de {nom} a expiré : les appareils affichent une alerte de sécurité. {conseil}"
    jours = reste.days
    if erreur and reste <= SEUIL_ECHEC:
        return (f"Le certificat HTTPS de {nom} expire dans {jours} jour(s) et son renouvellement "
                f"automatique échoue ({erreur}). {conseil}")
    if reste <= SEUIL_ABSOLU:
        return f"Le certificat HTTPS de {nom} expire dans {jours} jour(s) sans avoir été renouvelé. {conseil}"
    return None
