"""Poste de travail : l'accueil orienté tâches, adapté au rôle.

À la connexion, chaque personne voit 3 à 5 grandes actions formulées avec
les mots du métier (« Faire l'appel », « Accueillir une nouvelle personne »),
choisies selon son rôle puis filtrées par ses permissions réelles.

Principes :
- le RBAC reste la seule source de vérité : une action dont l'utilisateur
  n'a pas la permission n'est jamais affichée, quel que soit le rôle ;
- les gabarits par rôle ne sont qu'un ORDRE DE PRIORITÉ lisible ; un rôle
  personnalisé inconnu retombe sur un choix automatique par permissions ;
- les compteurs « en direct » (séances du jour, rappels ouverts) sont des
  requêtes COUNT bon marché, bornées au secteur de la personne, et toute
  erreur les transforme en simple absence de badge (l'accueil ne casse pas).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from flask import url_for
from werkzeug.routing import BuildError

from app.rbac import _expand_perm
from app.utils.dates import utcnow

JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

MAX_ACTIONS = 5


def _user_can(user, code: str) -> bool:
    """Comme le helper Jinja can(), mais pour un utilisateur explicite."""
    has_perm = getattr(user, "has_perm", None)
    if not callable(has_perm):
        return False
    return any(has_perm(c) for c in _expand_perm(code))


def _user_can_any(user, codes) -> bool:
    return any(_user_can(user, c) for c in codes or [])


def _scope_context(user) -> tuple[bool, str | None]:
    """Retourne ``(portée_globale, secteur)`` sans ambiguïté.

    Un secteur vide ne doit jamais être interprété comme une portée globale :
    c'est un compte à configurer, pas un passe-droit implicite.
    """
    has_all_scope = _user_can(user, "scope:all_secteurs")
    secteur = (getattr(user, "secteur_assigne", None) or "").strip() or None
    return has_all_scope, secteur


def _missing_scope_context() -> dict[str, Any]:
    return {
        "badge": "Secteur à configurer",
        "detail": "Aucune donnée globale n'est affichée tant que votre secteur n'est pas renseigné.",
        "tone": "warn",
        "count": None,
    }


# ---------------------------------------------------------------------------
# Compteurs « en direct » (badges des cartes)
# ---------------------------------------------------------------------------

def _ctx_seances_du_jour(user) -> dict[str, Any] | None:
    from app.extensions import db
    from app.models import SessionActivite

    today = date.today()
    has_all_scope, secteur = _scope_context(user)
    if not has_all_scope and not secteur:
        return _missing_scope_context()
    q = SessionActivite.query.filter(
        db.func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session) == today,
        SessionActivite.is_deleted.is_(False),
        SessionActivite.statut != "annulee",
    )
    if not has_all_scope:
        q = q.filter(SessionActivite.secteur == secteur)
    n = q.count()
    if n == 0:
        return {"badge": "Aucune séance aujourd'hui", "detail": "Le planning du jour est vide.", "tone": "ok", "count": 0}
    return {
        "badge": f"{n} séance{'s' if n > 1 else ''} aujourd'hui",
        "detail": "Ouvrez l'atelier pour faire l'appel.",
        "tone": "info",
        "count": n,
    }


def _ctx_emargements_en_attente(user) -> dict[str, Any] | None:
    from app.activite.saisie_grille import seances_sans_presence_query

    has_all_scope, secteur = _scope_context(user)
    if not has_all_scope and not secteur:
        return _missing_scope_context()
    n = seances_sans_presence_query(None if has_all_scope else secteur).count()
    if n == 0:
        return {"badge": "Tout est à jour", "detail": "Aucune feuille passée n'attend d'être saisie.", "tone": "ok", "count": 0}
    return {
        "badge": f"{n} séance{'s' if n > 1 else ''} à saisir",
        "detail": "Des feuilles de présence doivent être complétées.",
        "tone": "warn",
        "count": n,
    }


def _ctx_rappels_ouverts(user) -> dict[str, Any] | None:
    from app.extensions import db
    from app.models import SuiviRappel

    q = SuiviRappel.query.filter(SuiviRappel.statut == "ouvert")
    user_id = getattr(user, "id", None)
    has_all_scope, secteur = _scope_context(user)
    if has_all_scope:
        q = q.filter(db.or_(SuiviRappel.is_private.is_(False), SuiviRappel.created_by_user_id == user_id))
    elif secteur:
        q = q.filter(db.or_(
            SuiviRappel.created_by_user_id == user_id,
            SuiviRappel.is_private.is_(False) & (SuiviRappel.secteur == secteur),
        ))
    else:
        # Un compte sans secteur ne voit que ses propres rappels.
        q = q.filter(SuiviRappel.created_by_user_id == user_id)
    n = q.count()
    if n == 0:
        return {"badge": "Rien en attente", "detail": "Aucun rappel ouvert dans votre périmètre.", "tone": "ok", "count": 0}
    return {
        "badge": f"{n} rappel{'s' if n > 1 else ''} en attente",
        "detail": "Consultez les échéances et les points à reprendre.",
        "tone": "warn",
        "count": n,
    }


def _participant_scope_query(user):
    """Participants visibles pour les compteurs de l'accueil."""
    from app.models import Participant

    q = Participant.query
    has_all_scope, secteur = _scope_context(user)
    if has_all_scope or _user_can(user, "participants:view_all"):
        return q, None
    if not secteur:
        return None, _missing_scope_context()
    return q.filter(Participant.created_secteur == secteur), None


def _ctx_participants_recents(user) -> dict[str, Any] | None:
    from app.models import Participant

    q, blocked = _participant_scope_query(user)
    if blocked:
        return blocked
    cutoff = utcnow() - timedelta(days=7)
    n = q.filter(Participant.created_at.isnot(None), Participant.created_at >= cutoff).count()
    return {
        "badge": f"{n} nouvelle{'s' if n > 1 else ''} fiche{'s' if n > 1 else ''}",
        "detail": "Personnes ajoutées pendant les 7 derniers jours.",
        "tone": "info" if n else "ok",
        "count": n,
    }


def _ctx_fiches_incompletes(user) -> dict[str, Any] | None:
    from app.extensions import db
    from app.models import Participant

    q, blocked = _participant_scope_query(user)
    if blocked:
        return blocked
    n = q.filter(db.or_(
        Participant.date_naissance.is_(None),
        Participant.genre.is_(None),
        Participant.genre == "",
        Participant.ville.is_(None),
        Participant.ville == "",
        Participant.created_secteur.is_(None),
        Participant.created_secteur == "",
    )).count()
    return {
        "badge": f"{n} fiche{'s' if n > 1 else ''} à compléter" if n else "Toutes les fiches sont complètes",
        "detail": "Les données manquantes peuvent fragiliser les bilans." if n else "Aucun manque prioritaire détecté.",
        "tone": "warn" if n else "ok",
        "count": n,
    }


# ---------------------------------------------------------------------------
# Catalogue des actions métier (formulées en verbes, mots du quotidien)
# ---------------------------------------------------------------------------

POSTE_ACTIONS: dict[str, dict[str, Any]] = {
    "appel": {
        "label": "Faire l'appel",
        "desc": "Ouvrir mes séances et noter qui est présent.",
        "icon": "🖐️",
        "perm_any": ["emargement:view", "emargement:edit"],
        "endpoint": "activite.index",
        "context": _ctx_seances_du_jour,
    },
    "rattraper_emargements": {
        "label": "Rattraper les émargements",
        "desc": "Les feuilles papier en retard : voir ce qui manque, saisir en grille.",
        "icon": "🗓️",
        "perm_any": ["emargement:edit"],
        "endpoint": "activite.emargements_attente",
        "context": _ctx_emargements_en_attente,
    },
    "agenda": {
        "label": "Synchroniser mon agenda",
        "desc": "Retrouver mes séances dans Google Agenda ou Apple Calendrier.",
        "icon": "📅",
        "perm_any": ["emargement:view"],
        "endpoint": "main.mon_agenda",
    },
    "accueillir": {
        "label": "Accueillir une nouvelle personne",
        "desc": "Créer sa fiche en quelques champs, le reste peut attendre.",
        "icon": "🤝",
        "perm_any": ["participants:edit"],
        "endpoint": "participants.new_participant",
    },
    "retrouver": {
        "label": "Retrouver une fiche",
        "desc": "Chercher une personne et ouvrir son dossier.",
        "icon": "🔎",
        "perm_any": ["participants:view", "participants:view_all"],
        "endpoint": "participants.list_participants",
    },
    "a_traiter": {
        "label": "Voir ce qu'il y a à traiter",
        "desc": "Les rappels et points en attente, au même endroit.",
        "icon": "📌",
        "perm_any": ["dashboard:view"],
        "endpoint": "main.suivi_rappels",
        "context": _ctx_rappels_ouverts,
    },
    "suivre_activite": {
        "label": "Suivre l'activité",
        "desc": "Les chiffres de fréquentation et d'ateliers, en un coup d'œil.",
        "icon": "📊",
        "perm_any": ["statsimpact:view", "stats:view"],
        "endpoint": "statsimpact.dashboard",
    },
    "caisse": {
        "label": "Tenir la caisse",
        "desc": "Compter les espèces, faire le dépôt en banque, suivre le journal.",
        "icon": "💶",
        "perm_any": ["caisse:view", "caisse:edit"],
        "endpoint": "main.caisse",
    },
    "saisir_depense": {
        "label": "Saisir une dépense",
        "desc": "Enregistrer une facture ou un achat sur la bonne enveloppe.",
        "icon": "🧾",
        "perm_any": ["depenses:create", "depenses:edit"],
        "endpoint": "budget.depenses_list",
    },
    "suivre_subventions": {
        "label": "Suivre les subventions",
        "desc": "Les enveloppes, ce qui est reçu et ce qui reste.",
        "icon": "💰",
        "perm_any": ["subventions:view"],
        "endpoint": "main.subventions_list",
    },
    "preparer_bilan": {
        "label": "Préparer un bilan",
        "desc": "Les chiffres prêts à copier pour la CAF, la ville ou le CA.",
        "icon": "📑",
        "perm_any": ["stats:view", "bilans:view"],
        "endpoint": "main.stats_bilans",
        "fallback_endpoint": "main.hub_bilans",
    },
    "monter_projet": {
        "label": "Monter un projet",
        "desc": "Créer une action, son budget et sa demande de financement.",
        "icon": "🧱",
        "perm_any": ["projets:edit"],
        "endpoint": "projets.projets_list",
    },
    "suivre_apprentissages": {
        "label": "Suivre les apprentissages",
        "desc": "Les passeports et les progrès des participants.",
        "icon": "🎓",
        "perm_any": ["pedagogie:view"],
        "endpoint": "pedagogie.suivi_pedagogique",
    },
    "gerer_equipe": {
        "label": "Gérer l'équipe",
        "desc": "Comptes, rôles et secteurs des utilisateurs.",
        "icon": "👥",
        "perm_any": ["admin:users"],
        "endpoint": "admin.users",
    },
    "regler_droits": {
        "label": "Régler les droits d'accès",
        "desc": "Qui a le droit de voir ou modifier quoi.",
        "icon": "🔐",
        "perm_any": ["admin:rbac"],
        "endpoint": "admin.droits",
    },
    "verifier_sante": {
        "label": "Vérifier la santé de l'application",
        "desc": "Contrôles techniques, liens cassés, base de données.",
        "icon": "🩺",
        "perm_any": ["admin:rbac", "controle:view"],
        "endpoint": "admin.sante_systeme",
        "fallback_endpoint": "main.controle",
    },
    "verifier_sauvegardes": {
        "label": "Vérifier les sauvegardes",
        "desc": "S'assurer que les données sont bien protégées.",
        "icon": "💾",
        "perm_any": ["admin:rbac"],
        "endpoint": "admin.sauvegardes",
    },
}


DAILY_SIGNAL_SPECS: list[dict[str, Any]] = [
    {
        "key": "today_sessions",
        "label": "Séances aujourd'hui",
        "icon": "📅",
        "perm_any": ["emargement:view"],
        "endpoint": "activite.index",
        "context": _ctx_seances_du_jour,
    },
    {
        "key": "attendance_backlog",
        "label": "Présences à compléter",
        "icon": "🖊️",
        "perm_any": ["emargement:edit"],
        "endpoint": "activite.emargements_attente",
        "context": _ctx_emargements_en_attente,
    },
    {
        "key": "recent_participants",
        "label": "Nouvelles fiches · 7 jours",
        "icon": "👤",
        "perm_any": ["participants:view", "participants:view_all"],
        "endpoint": "participants.list_participants",
        "context": _ctx_participants_recents,
    },
    {
        "key": "participant_quality",
        "label": "Fiches à compléter",
        "icon": "🧩",
        "perm_any": ["participants:view", "participants:view_all"],
        "endpoint": "main.qualite_donnees_transverse",
        "endpoint_values": {"famille": "participants"},
        "context": _ctx_fiches_incompletes,
    },
    {
        "key": "open_reminders",
        "label": "Rappels ouverts",
        "icon": "📌",
        "perm_any": ["dashboard:view"],
        "endpoint": "main.suivi_rappels",
        "context": _ctx_rappels_ouverts,
    },
]


# Gabarits par rôle : un ordre de priorité lisible, PAS un droit d'accès.
# Le filtre par permission s'applique toujours derrière. Les codes non
# présents dans ROLE_TEMPLATES (animateur, accueil…) couvrent les rôles
# personnalisés couramment créés via l'interface d'administration.
ROLE_POSTES: dict[str, list[str]] = {
    "direction": ["a_traiter", "suivre_activite", "preparer_bilan", "suivre_subventions", "gerer_equipe"],
    "finance": ["a_traiter", "saisir_depense", "suivre_subventions", "preparer_bilan", "monter_projet"],
    "responsable_secteur": ["appel", "accueillir", "a_traiter", "suivre_activite", "monter_projet"],
    "coordinateur": ["appel", "accueillir", "a_traiter", "suivre_activite", "monter_projet"],
    "animateur": ["appel", "accueillir", "retrouver", "suivre_apprentissages"],
    "accueil": ["accueillir", "retrouver", "rattraper_emargements", "appel", "a_traiter"],
    "secretaire": ["accueillir", "retrouver", "rattraper_emargements", "appel", "a_traiter"],
    "benevole": ["appel", "retrouver"],
    "admin_tech": ["gerer_equipe", "regler_droits", "verifier_sante", "verifier_sauvegardes"],
}

# Ordre de repli pour un rôle inconnu : du plus quotidien au plus pilotage.
FALLBACK_ORDER = [
    "appel", "accueillir", "retrouver", "rattraper_emargements", "a_traiter", "suivre_activite",
    "caisse", "saisir_depense", "suivre_subventions", "preparer_bilan", "monter_projet",
    "suivre_apprentissages", "gerer_equipe", "regler_droits",
]

# Priorité de résolution quand une personne cumule plusieurs rôles.
ROLE_PRIORITY = [
    "direction", "finance", "responsable_secteur", "coordinateur",
    "animateur", "accueil", "secretaire", "benevole", "admin_tech",
]


def _safe_url(endpoint: str | None, fallback_endpoint: str | None = None, **values) -> str | None:
    for candidate in (endpoint, fallback_endpoint):
        if not candidate:
            continue
        try:
            return url_for(candidate, **values)
        except BuildError:
            continue
    return None


def _build_daily_signals(user) -> list[dict[str, Any]]:
    """Compteurs compacts de l'accueil, toujours filtrés par droits et secteur."""
    signals: list[dict[str, Any]] = []
    scope_warning_added = False
    for spec in DAILY_SIGNAL_SPECS:
        if not _user_can_any(user, spec.get("perm_any")):
            continue
        try:
            ctx = spec["context"](user) or {}
        except Exception:
            # Comme pour les cartes : un compteur indisponible ne bloque pas l'accueil.
            continue

        if ctx.get("count") is None:
            if scope_warning_added:
                continue
            scope_warning_added = True
            signals.append({
                "key": "missing_scope",
                "label": "Périmètre à configurer",
                "icon": "⚠️",
                "value": "—",
                "detail": ctx.get("detail") or "Demandez à un administrateur de renseigner votre secteur.",
                "tone": "warn",
                "url": None,
            })
            continue

        signals.append({
            "key": spec["key"],
            "label": spec["label"],
            "icon": spec["icon"],
            "value": int(ctx.get("count") or 0),
            "detail": ctx.get("detail") or ctx.get("badge") or "",
            "tone": ctx.get("tone") or "muted",
            "url": _safe_url(spec.get("endpoint"), **(spec.get("endpoint_values") or {})),
        })
    return signals


def _matched_role(user) -> str | None:
    has_role = getattr(user, "has_role", None)
    if not callable(has_role):
        return None
    for code in ROLE_PRIORITY:
        try:
            if has_role(code):
                return code
        except Exception:
            continue
    return None


def _role_label(user) -> str:
    """Libellé humain du premier rôle RBAC de la personne."""
    try:
        roles = list(getattr(user, "roles", []) or [])
        if roles:
            return (getattr(roles[0], "label", None) or roles[0].code or "").strip()
    except Exception:
        pass
    return ""


def date_du_jour_fr(today: date | None = None) -> str:
    today = today or date.today()
    return f"{JOURS_FR[today.weekday()]} {today.day} {MOIS_FR[today.month - 1]} {today.year}"


def build_poste_travail(user) -> dict[str, Any]:
    """Construit le bloc « Ma journée » : actions du rôle + compteurs live."""
    role_code = _matched_role(user)
    keys = list(ROLE_POSTES.get(role_code or "", [])) or list(FALLBACK_ORDER)

    actions: list[dict[str, Any]] = []
    for key in keys:
        if len(actions) >= MAX_ACTIONS:
            break
        meta = POSTE_ACTIONS.get(key)
        if not meta:
            continue
        if not _user_can_any(user, meta.get("perm_any")):
            continue
        url = _safe_url(meta.get("endpoint"), meta.get("fallback_endpoint"))
        if not url:
            continue
        item: dict[str, Any] = {
            "key": key,
            "label": meta["label"],
            "desc": meta.get("desc") or "",
            "icon": meta.get("icon") or "➡️",
            "url": url,
            "badge": None,
            "tone": "muted",
        }
        ctx_fn = meta.get("context")
        if callable(ctx_fn):
            try:
                ctx = ctx_fn(user) or {}
                item["badge"] = ctx.get("badge")
                item["tone"] = ctx.get("tone") or "muted"
            except Exception:
                # Un compteur cassé ne doit jamais casser l'accueil.
                pass
        actions.append(item)

    return {
        "actions": actions,
        "signals": _build_daily_signals(user),
        "role_code": role_code,
        "role_label": _role_label(user),
        "secteur": (getattr(user, "secteur_assigne", None) or "").strip() or None,
        "prenom": (getattr(user, "nom", None) or "").strip(),
        "date_fr": date_du_jour_fr(),
    }
