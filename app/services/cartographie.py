"""Agrégation géographique des participants pour la carte (par quartier).

RGPD : on n'expose JAMAIS un domicile individuel. Les participants
géolocalisés sont regroupés par quartier ; le point d'un quartier est le
centroïde (moyenne) des points de ses habitants localisés. Les participants
localisés sans quartier forment un groupe « Sans quartier ». Les participants
non géolocalisés sont seulement comptés (jamais positionnés).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func

from app.extensions import db
from app.models import Participant, PresenceActivite, Quartier, SessionActivite


def _date_session_expr():
    """Date effective d'une session (rdv sinon date collective)."""
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def annees_de_presence() -> list[int]:
    """Années (décroissant) où au moins une présence a été enregistrée.

    Sert à alimenter le filtre « période de présence » (carte et stats).
    """
    sd = _date_session_expr()
    rows = (
        db.session.query(func.extract("year", sd))
        .join(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(sd.isnot(None))
        .distinct()
        .all()
    )
    return sorted({int(a) for (a,) in rows if a is not None}, reverse=True)


def _ids_presents(date_from, date_to):
    """IDs des participants ayant ≥1 présence sur la période [date_from, date_to]."""
    sd = _date_session_expr()
    q = (
        db.session.query(PresenceActivite.participant_id)
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
    )
    if date_from:
        q = q.filter(sd >= date_from)
    if date_to:
        q = q.filter(sd <= date_to)
    return q.distinct()

# Centre de Creil par défaut (utilisé si aucun participant n'est localisé).
CENTRE_DEFAUT = {"lat": 49.2583, "lon": 2.4750}


def repartition_par_quartier(secteur=None, type_public=None, date_from=None, date_to=None) -> dict:
    """Retourne l'agrégat par quartier, prêt à sérialiser en JSON pour la carte.

    Filtres optionnels : ``secteur`` (Participant.created_secteur),
    ``type_public`` (Participant.type_public) et surtout la **période de
    présence active** : si ``date_from``/``date_to`` sont fournis, on ne compte
    que les habitants ayant participé (≥1 présence) sur cette période — un même
    habitant compte donc dans chaque période où il est revenu, pas selon son
    année de création.
    """
    q = Participant.query
    if type_public:
        q = q.filter(Participant.type_public == type_public)
    if secteur:
        q = q.filter(Participant.created_secteur == secteur)
    if date_from or date_to:
        q = q.filter(Participant.id.in_(_ids_presents(date_from, date_to)))

    participants = q.all()
    quartiers = {qq.id: qq for qq in Quartier.query.all()}

    groupes: dict = {}
    total = 0
    for p in participants:
        total += 1
        # Regroupement par quartier si renseigné, sinon par commune (ville).
        if p.quartier_id:
            cle = ("q", p.quartier_id)
        elif (p.ville or "").strip():
            cle = ("v", p.ville.strip())
        else:
            cle = ("sans",)
        g = groupes.get(cle)
        if g is None:
            q_lat = q_lon = None
            if cle[0] == "q":
                qq = quartiers.get(cle[1])
                nom = qq.nom if qq else "Quartier inconnu"
                ville = qq.ville if qq else ""
                is_qpv = bool(qq.is_qpv) if qq else False
                q_lat = qq.latitude if qq else None
                q_lon = qq.longitude if qq else None
            elif cle[0] == "v":
                nom, ville, is_qpv = cle[1], cle[1], False
            else:
                nom, ville, is_qpv = "Sans localisation", "", False
            g = groupes[cle] = {
                "id": cle[1] if cle[0] == "q" else None,
                "nom": nom,
                "ville": ville,
                "is_qpv": is_qpv,
                "total": 0,
                # Position : coordonnées propres du quartier (prioritaires)…
                "q_lat": q_lat,
                "q_lon": q_lon,
                # …sinon moyenne des adresses géocodées des membres.
                "_lat": 0.0,
                "_lon": 0.0,
                "_n": 0,
                "par_secteur": defaultdict(int),
                "par_type_public": defaultdict(int),
            }
        g["total"] += 1
        g["par_secteur"][p.created_secteur or "—"] += 1
        g["par_type_public"][p.type_public or "—"] += 1
        if p.latitude is not None and p.longitude is not None:
            g["_lat"] += p.latitude
            g["_lon"] += p.longitude
            g["_n"] += 1

    quartiers_out = []
    non_localises = 0
    for g in groupes.values():
        # Coordonnées du quartier en priorité ; sinon moyenne des adresses ;
        # sinon le groupe n'a aucune position connue (compté « non localisé »).
        if g["q_lat"] is not None and g["q_lon"] is not None:
            lat, lon = g["q_lat"], g["q_lon"]
        elif g["_n"] > 0:
            lat, lon = g["_lat"] / g["_n"], g["_lon"] / g["_n"]
        else:
            non_localises += g["total"]
            continue
        quartiers_out.append(
            {
                "id": g["id"],
                "nom": g["nom"],
                "ville": g["ville"],
                "is_qpv": g["is_qpv"],
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "total": g["total"],
                "par_secteur": dict(sorted(g["par_secteur"].items(), key=lambda x: -x[1])),
                "par_type_public": dict(sorted(g["par_type_public"].items(), key=lambda x: -x[1])),
            }
        )
    quartiers_out.sort(key=lambda x: -x["total"])

    if quartiers_out:
        centre = {
            "lat": sum(x["lat"] for x in quartiers_out) / len(quartiers_out),
            "lon": sum(x["lon"] for x in quartiers_out) / len(quartiers_out),
        }
    else:
        centre = dict(CENTRE_DEFAUT)

    return {
        "centre": centre,
        "quartiers": quartiers_out,
        "total": total,
        "localises": total - non_localises,
        "non_localises": non_localises,
    }


def liste_partenaires(secteur=None) -> dict:
    """Liste des partenaires géolocalisés (un point cliquable par structure).

    Contrairement aux habitants, un partenaire est une structure (information
    publique) : on affiche donc des marqueurs individuels. Filtre optionnel
    par secteur d'intervention.
    """
    from flask import has_request_context, url_for

    from app.models import Partenaire, PartenaireSecteur

    def _fiche_url(pid):
        # url_for nécessite un contexte de requête ; hors requête (tâche/tests),
        # on construit le chemin relatif (le préfixe du blueprint est fixe).
        if has_request_context():
            return url_for("partenaires.edit", partenaire_id=pid)
        return f"/partenaires/{pid}/edit"

    try:
        from app.partenaires.routes import ORIENTATION_DOMAINES
    except Exception:  # pragma: no cover - dégradation si import indisponible
        ORIENTATION_DOMAINES = {}

    q = Partenaire.query
    if secteur:
        q = q.join(PartenaireSecteur).filter(PartenaireSecteur.secteur == secteur)
    partenaires = q.order_by(Partenaire.nom.asc()).distinct().all()

    points = []
    non_localises = 0
    for p in partenaires:
        if p.latitude is None or p.longitude is None:
            non_localises += 1
            continue
        points.append(
            {
                "id": p.id,
                "nom": p.nom,
                "adresse": p.adresse,
                "lat": p.latitude,
                "lon": p.longitude,
                "secteurs": [s.secteur for s in p.secteurs],
                "competences": [ORIENTATION_DOMAINES.get(c, c) for c in p.competences_orientation()],
                "tel": p.tel_general or p.tel_contact,
                "email": p.email_general or p.email_contact,
                "fiche_url": _fiche_url(p.id),
            }
        )

    if points:
        centre = {
            "lat": sum(x["lat"] for x in points) / len(points),
            "lon": sum(x["lon"] for x in points) / len(points),
        }
    else:
        centre = dict(CENTRE_DEFAUT)

    return {
        "centre": centre,
        "partenaires": points,
        "total": len(partenaires),
        "localises": len(points),
        "non_localises": non_localises,
    }
