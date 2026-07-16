"""Flux calendrier iCal (RFC 5545) des séances ET du temps de travail,
personnalisable, pour abonnement Google Agenda / Apple / Outlook ou export
ponctuel par période.

Contexte assumé : pour beaucoup de financeurs (ex. plateforme CSAT),
l'agenda EST la feuille de temps — il est extrait automatiquement chaque
mois. Le flux doit donc pouvoir refléter TOUT le temps effectif :
- les séances d'ateliers (bornées au secteur, ou étendues) ;
- les temps forts/événements de tous les secteurs (option) ;
- les créneaux hors ateliers (réunions, préparation, formation…) saisis
  dans l'application ;
- un temps de préparation automatique avant chaque séance (option).

Et chacun contrôle ce qui s'affiche : format du titre (jetons), lignes de
la description, fenêtre de temps, séances annulées ou non. Jamais de nom
de participant : seuls des agrégats (compteur de présences) sortent.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import (
    AgendaCreneau,
    AgendaPreference,
    AtelierActivite,
    PresenceActivite,
    SessionActivite,
)
from app.utils.dates import utcnow

# ---------------------------------------------------------------------------
# Options du flux (JSON par utilisateur, valeurs sûres par défaut)
# ---------------------------------------------------------------------------

CHAMPS_DESCRIPTION = ["type", "horaire", "capacite", "presences", "emargement", "secteur"]
CHAMPS_DESCRIPTION_LABELS = {
    "type": "Type de séance (collective / individuelle / événement)",
    "horaire": "Horaire",
    "capacite": "Capacité de l'atelier",
    "presences": "Nombre de présences saisies (jamais les noms)",
    "emargement": "Rappel « émargement à faire » sur les séances passées",
    "secteur": "Secteur",
}

TITRE_JETONS = ["{atelier}", "{secteur}", "{type}", "{heure}"]
TITRE_PRESETS = [
    ("{atelier}", "Nom de l'atelier seul"),
    ("{atelier} · {secteur}", "Atelier · Secteur"),
    ("{type} — {atelier}", "Type — Atelier"),
    ("{atelier} ({heure})", "Atelier (heure de début)"),
]

OPTIONS_DEFAUT: dict = {
    "titre_format": "{atelier}",
    "champs_description": list(CHAMPS_DESCRIPTION),
    "inclure_lien": True,
    "inclure_annulees": True,
    "evenements_tous_secteurs": False,
    "inclure_creneaux": True,
    "preparation_minutes": 0,
    "jours_passe": 30,
    "jours_futur": 180,
}


def charger_options(user) -> dict:
    """Options du flux de la personne (défauts complétés champ par champ)."""
    options = dict(OPTIONS_DEFAUT)
    pref = getattr(user, "agenda_pref", None)
    if pref and pref.options_json:
        try:
            enregistre = json.loads(pref.options_json)
            if isinstance(enregistre, dict):
                options.update({k: v for k, v in enregistre.items() if k in OPTIONS_DEFAUT})
        except Exception:
            pass
    return _assainir_options(options)


def _assainir_options(options: dict) -> dict:
    """Bornes et types sûrs, quoi qu'il y ait en base ou dans le POST."""
    o = dict(OPTIONS_DEFAUT)
    o.update(options or {})
    o["titre_format"] = str(o.get("titre_format") or "{atelier}")[:120] or "{atelier}"
    champs = o.get("champs_description")
    if not isinstance(champs, list):
        champs = list(CHAMPS_DESCRIPTION)
    o["champs_description"] = [c for c in champs if c in CHAMPS_DESCRIPTION]
    for cle in ("inclure_lien", "inclure_annulees", "evenements_tous_secteurs", "inclure_creneaux"):
        o[cle] = bool(o.get(cle))
    try:
        o["preparation_minutes"] = max(0, min(240, int(o.get("preparation_minutes") or 0)))
    except Exception:
        o["preparation_minutes"] = 0
    try:
        o["jours_passe"] = max(0, min(366, int(o.get("jours_passe") or 30)))
    except Exception:
        o["jours_passe"] = 30
    try:
        o["jours_futur"] = max(7, min(366, int(o.get("jours_futur") or 180)))
    except Exception:
        o["jours_futur"] = 180
    return o


def sauvegarder_options(user, options: dict) -> dict:
    options = _assainir_options(options)
    pref = getattr(user, "agenda_pref", None)
    if pref is None:
        pref = AgendaPreference(user_id=user.id)
        db.session.add(pref)
    pref.options_json = json.dumps(options, ensure_ascii=False)
    db.session.commit()
    return options


# ---------------------------------------------------------------------------
# Jeton du flux
# ---------------------------------------------------------------------------

def token_ou_creer(user) -> str:
    """Jeton du flux de la personne (créé et persisté au premier appel)."""
    if not getattr(user, "calendar_token", None):
        user.calendar_token = secrets.token_urlsafe(24)
        db.session.commit()
    return user.calendar_token


def regenerer_token(user) -> str:
    """Change le jeton : l'ancien lien cesse immédiatement de fonctionner."""
    user.calendar_token = secrets.token_urlsafe(24)
    db.session.commit()
    return user.calendar_token


# ---------------------------------------------------------------------------
# Sélection des données
# ---------------------------------------------------------------------------

def _secteur_du_flux(user) -> str | None:
    """Secteur filtré pour ce flux (None = tous, pour la portée globale)."""
    has_perm = getattr(user, "has_perm", None)
    if callable(has_perm) and has_perm("scope:all_secteurs"):
        return None
    return (getattr(user, "secteur_assigne", None) or "").strip() or None


def sessions_du_flux(user, options: dict, *, du: date, au: date) -> list[SessionActivite]:
    eff = db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
    base = (
        SessionActivite.query
        .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
        .filter(
            SessionActivite.is_deleted.is_(False),
            AtelierActivite.is_deleted.is_(False),
            eff >= du,
            eff <= au,
        )
    )
    if not options.get("inclure_annulees", True):
        base = base.filter(db.func.lower(db.func.coalesce(SessionActivite.statut, "")) != "annulee")

    secteur = _secteur_du_flux(user)
    if secteur:
        if options.get("evenements_tous_secteurs"):
            # Mon secteur + les temps forts (événements) de TOUS les secteurs.
            q = base.filter(db.or_(
                SessionActivite.secteur == secteur,
                SessionActivite.est_evenement.is_(True),
            ))
        else:
            q = base.filter(SessionActivite.secteur == secteur)
    else:
        q = base
    return q.order_by(eff.asc()).all()


def creneaux_du_flux(user, *, du: date, au: date) -> list[AgendaCreneau]:
    return (
        AgendaCreneau.query
        .filter(
            AgendaCreneau.user_id == user.id,
            AgendaCreneau.date_creneau >= du,
            AgendaCreneau.date_creneau <= au,
        )
        .order_by(AgendaCreneau.date_creneau.asc(), AgendaCreneau.id.asc())
        .all()
    )


def _presences_par_session(ids: list[int]) -> dict[int, int]:
    """Nombre de présences saisies par séance, en une seule requête."""
    if not ids:
        return {}
    rows = (
        db.session.query(PresenceActivite.session_id, db.func.count(PresenceActivite.id))
        .filter(PresenceActivite.session_id.in_(ids))
        .group_by(PresenceActivite.session_id)
        .all()
    )
    return {sid: n for sid, n in rows}


# ---------------------------------------------------------------------------
# Formatage iCalendar
# ---------------------------------------------------------------------------

def _echapper(texte: str) -> str:
    return (
        (texte or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _plier(ligne: str) -> str:
    """Pliage RFC 5545 : lignes de contenu ≤ 75 octets, suite préfixée d'un
    espace. On plie sur les octets (UTF-8) pour ne jamais dépasser."""
    octets = ligne.encode("utf-8")
    if len(octets) <= 75:
        return ligne
    morceaux, courant = [], b""
    for ch in ligne:
        b = ch.encode("utf-8")
        limite = 75 if not morceaux else 74
        if len(courant) + len(b) > limite:
            morceaux.append(courant.decode("utf-8"))
            courant = b
        else:
            courant += b
    if courant:
        morceaux.append(courant.decode("utf-8"))
    return "\r\n ".join(morceaux)


def _dt_local(d: date, heure: str | None) -> tuple[str, bool]:
    if heure:
        try:
            hh, mm = heure.strip().split(":")[:2]
            return f"{d.strftime('%Y%m%d')}T{int(hh):02d}{int(mm):02d}00", False
        except Exception:
            pass
    return d.strftime("%Y%m%d"), True


def _plus_une_heure(heure: str | None) -> str | None:
    if not heure:
        return None
    try:
        hh, mm = heure.strip().split(":")[:2]
        base = datetime(2000, 1, 1, int(hh), int(mm)) + timedelta(hours=1)
        return base.strftime("%H:%M")
    except Exception:
        return None


def _moins_minutes(heure: str, minutes: int) -> str | None:
    try:
        hh, mm = heure.strip().split(":")[:2]
        base = datetime(2000, 1, 2, int(hh), int(mm)) - timedelta(minutes=minutes)
        if base.day != 2:  # déborde sur la veille : on n'invente pas
            return None
        return base.strftime("%H:%M")
    except Exception:
        return None


def _type_seance(s) -> str:
    if getattr(s, "est_evenement", False):
        return "Événement / temps fort 🎉"
    return "Séance individuelle" if s.session_type != "COLLECTIF" else "Séance collective"


def _rendre_titre(gabarit: str, *, atelier: str, secteur: str, type_seance: str, heure: str) -> str:
    """Remplace UNIQUEMENT les jetons connus (pas de str.format : un gabarit
    contenant {n'importe quoi} ne doit jamais faire d'erreur)."""
    rendu = gabarit or "{atelier}"
    for jeton, valeur in (("{atelier}", atelier), ("{secteur}", secteur),
                          ("{type}", type_seance), ("{heure}", heure)):
        rendu = rendu.replace(jeton, valeur)
    rendu = rendu.strip() or atelier
    return rendu[:200]


def _description_seance(s, atelier, presences: int, options: dict) -> str:
    champs = options.get("champs_description") or []
    today = date.today()
    d = s.rdv_date or s.date_session
    heure_debut = s.rdv_debut or s.heure_debut
    heure_fin = s.rdv_fin or s.heure_fin
    lignes = []
    if "type" in champs:
        lignes.append(_type_seance(s))
    if "horaire" in champs and heure_debut:
        lignes.append(f"Horaire : {heure_debut}" + (f"–{heure_fin}" if heure_fin else ""))
    if "capacite" in champs and atelier and atelier.capacite_defaut:
        lignes.append(f"Capacité : {atelier.capacite_defaut} places")
    if "presences" in champs and presences:
        lignes.append(f"Présences saisies : {presences}")
    elif "emargement" in champs and not presences and d and d < today and (s.statut or "").lower() != "annulee":
        lignes.append("⏳ Émargement à faire")
    if "secteur" in champs:
        lignes.append(f"Secteur : {s.secteur or '—'}")
    return "\n".join(lignes)


def _evenement(lignes: list[str], *, uid: str, stamp: str, d: date,
               heure_debut: str | None, heure_fin: str | None,
               titre: str, lieu: str, description: str,
               url: str | None = None, annule: bool = False) -> None:
    deb_val, all_day = _dt_local(d, heure_debut)
    lignes.append("BEGIN:VEVENT")
    lignes.append(f"UID:{uid}")
    lignes.append(f"DTSTAMP:{stamp}")
    if all_day:
        lignes.append(f"DTSTART;VALUE=DATE:{deb_val}")
        lignes.append(f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}")
    else:
        fin_val, _ = _dt_local(d, heure_fin or _plus_une_heure(heure_debut))
        lignes.append(f"DTSTART;TZID=Europe/Paris:{deb_val}")
        lignes.append(f"DTEND;TZID=Europe/Paris:{fin_val}")
    lignes.append(_plier(f"SUMMARY:{_echapper(titre)}"))
    if lieu:
        lignes.append(_plier(f"LOCATION:{_echapper(lieu)}"))
    if description:
        lignes.append(_plier(f"DESCRIPTION:{_echapper(description)}"))
    if url:
        lignes.append(_plier(f"URL:{url}"))
    lignes.append("STATUS:CANCELLED" if annule else "STATUS:CONFIRMED")
    lignes.append("END:VEVENT")


def generer_ics(user, *, base_url: str, lien_base: str = "",
                nom_calendrier: str = "Mes séances",
                options: dict | None = None,
                du: date | None = None, au: date | None = None) -> str:
    """Génère le flux. ``du``/``au`` (export ponctuel) priment sur la
    fenêtre des options ; sinon fenêtre glissante autour d'aujourd'hui."""
    options = _assainir_options(options if options is not None else charger_options(user))
    today = date.today()
    du = du or today - timedelta(days=options["jours_passe"])
    au = au or today + timedelta(days=options["jours_futur"])

    host = (base_url or "").split("//", 1)[-1].split("/", 1)[0] or "erp.local"
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    lien_base = (lien_base or "").rstrip("/")

    lignes = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Gestion du Centre//Sceances//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_echapper(nom_calendrier)}",
        "X-WR-TIMEZONE:Europe/Paris",
    ]

    seances = sessions_du_flux(user, options, du=du, au=au)
    presences_par_sid = _presences_par_session([s.id for s in seances])
    prep = options["preparation_minutes"]

    for s in seances:
        d = s.rdv_date or s.date_session
        if d is None:
            continue
        heure_debut = s.rdv_debut or s.heure_debut
        heure_fin = s.rdv_fin or s.heure_fin or _plus_une_heure(heure_debut)
        atelier = s.atelier
        nom_atelier = atelier.nom if atelier else f"Atelier #{s.atelier_id}"
        annule = (s.statut or "").strip().lower() == "annulee"

        titre = _rendre_titre(
            options["titre_format"],
            atelier=nom_atelier, secteur=s.secteur or "",
            type_seance=_type_seance(s), heure=heure_debut or "",
        )
        if getattr(s, "est_evenement", False) and "🎉" not in titre:
            titre = f"🎉 {titre}"

        _evenement(
            lignes, uid=f"seance-{s.id}@{host}", stamp=stamp, d=d,
            heure_debut=heure_debut, heure_fin=heure_fin,
            titre=titre, lieu=(s.secteur or "").strip(),
            description=_description_seance(s, atelier, presences_par_sid.get(s.id, 0), options),
            url=(f"{lien_base}/activite/session/{s.id}/emargement"
                 if lien_base and options.get("inclure_lien") else None),
            annule=annule,
        )

        # Temps de préparation automatique AVANT la séance (temps effectif).
        if prep and heure_debut and not annule:
            debut_prep = _moins_minutes(heure_debut, prep)
            if debut_prep:
                _evenement(
                    lignes, uid=f"seance-{s.id}-prep@{host}", stamp=stamp, d=d,
                    heure_debut=debut_prep, heure_fin=heure_debut,
                    titre=f"Préparation — {nom_atelier}",
                    lieu=(s.secteur or "").strip(),
                    description=f"Temps de préparation ({prep} min) avant la séance.",
                )

    if options.get("inclure_creneaux", True):
        for c in creneaux_du_flux(user, du=du, au=au):
            _evenement(
                lignes, uid=f"creneau-{c.id}@{host}", stamp=stamp, d=c.date_creneau,
                heure_debut=c.heure_debut, heure_fin=c.heure_fin,
                titre=f"{c.type_label} — {c.titre}",
                lieu="", description=(c.description or "").strip(),
            )

    lignes.append("END:VCALENDAR")
    return "\r\n".join(lignes) + "\r\n"


def apercu_evenement(options: dict) -> dict:
    """Exemple rendu selon les réglages (aperçu sur la page Mon agenda)."""
    options = _assainir_options(options)

    class _Exemple:
        session_type = "COLLECTIF"
        est_evenement = False
        statut = "realisee"
        secteur = "Jeunesse"
        rdv_date = None
        rdv_debut = None
        rdv_fin = None
        date_session = date.today()
        heure_debut = "14:00"
        heure_fin = "16:00"

    class _AtelierExemple:
        nom = "Cours de français (ASL)"
        capacite_defaut = 15

    exemple = _Exemple()
    titre = _rendre_titre(options["titre_format"], atelier=_AtelierExemple.nom,
                          secteur="Jeunesse", type_seance="Séance collective", heure="14:00")
    description = _description_seance(exemple, _AtelierExemple, 8, options)
    return {"titre": titre, "lignes": description.split("\n") if description else []}
