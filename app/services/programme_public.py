"""Programme public des activités.

Produit une liste **non nominative** des ateliers collectifs à venir, destinée
à être exportée en page HTML autonome et publiée sur un site / hébergement
externe. Aucune donnée personnelle (participants) n'y figure.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models import AtelierActivite, SessionActivite

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def label_date_fr(d: date) -> str:
    return f"{_JOURS[d.weekday()]} {d.day} {_MOIS[d.month - 1]} {d.year}"


def sessions_a_venir(jours: int = 60) -> list[dict]:
    """Ateliers COLLECTIFS programmés d'aujourd'hui à +``jours``, groupés par date.

    Retourne une liste de ``{"label": "lundi 1 juin 2026", "sessions": [...]}``,
    triée par date croissante. Les rendez-vous individuels sont volontairement
    exclus (ils ne relèvent pas d'un programme public).
    """
    today = date.today()
    limite = today + timedelta(days=jours)
    sessions = (
        SessionActivite.query.filter_by(session_type="COLLECTIF")
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(SessionActivite.date_session >= today)
        .filter(SessionActivite.date_session <= limite)
        .order_by(SessionActivite.date_session.asc(), SessionActivite.heure_debut.asc())
        .all()
    )

    groupes: dict[date, list[dict]] = {}
    for s in sessions:
        atelier = db.session.get(AtelierActivite, s.atelier_id)
        groupes.setdefault(s.date_session, []).append(
            {
                "atelier": atelier.nom if atelier else "Atelier",
                "secteur": s.secteur,
                "debut": s.heure_debut,
                "fin": s.heure_fin,
            }
        )

    return [
        {"label": label_date_fr(d), "sessions": items}
        for d, items in sorted(groupes.items())
    ]


def rendu_html() -> str:
    """Rend la page programme (HTML autonome) en chaîne de caractères.

    Réutilisée par le téléchargement manuel et par la publication automatique.
    """
    from flask import current_app, has_request_context, render_template

    structure = (
        current_app.config.get("ORGANIZATION_NAME")
        or current_app.config.get("APP_NAME")
        or "Centre social"
    )

    def _render() -> str:
        return render_template(
            "exports/programme_public.html",
            groupes=sessions_a_venir(jours=60),
            structure=structure,
            genere_le=date.today(),
        )

    # Le rendu peut être déclenché hors requête HTTP (tâche planifiée, CLI) :
    # on fournit alors un contexte de requête temporaire, sinon les context
    # processors qui lisent request.endpoint échouent.
    if has_request_context():
        return _render()
    with current_app.test_request_context():
        return _render()
