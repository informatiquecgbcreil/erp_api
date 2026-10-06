"""E-mails de l'espace salarié : prévenir la bonne personne, au bon moment.

Complète le récapitulatif d'Administration → Notifications (pilotage, adresses
fixes) par des e-mails PERSONNELS envoyés au fil de l'eau, quand quelqu'un
attend une réponse :

- demande de récupération signée → l'assistant·e de direction (à défaut la
  direction) ;
- demande transmise → la direction, et le salarié (« transmise ») ;
- demande refusée par l'assistant·e → le salarié, avec la justification ;
- décision de la direction → le salarié (justification en cas de refus) et
  l'assistant·e, qui doit en prendre connaissance ;
- heures sup corrigées ou retirées → le salarié, avec la justification ;
- document déposé dans le coffre-fort → chaque personne de la liste d'accès.

Principes :
- l'e-mail est écrit dans la file ``courriel_rh`` DANS la transaction de
  l'action, puis envoyé juste après le commit : une action annulée ne
  prévient personne, un serveur de mail absent ne bloque jamais l'action ;
- un échec est réessayé (prochaine action RH, puis chaque jour) au plus
  ``MAX_TENTATIVES`` fois ; l'état de la file est visible dans
  Administration → Notifications ;
- jamais de donnée sensible dans le corps (pas de salaire, pas de document) :
  un fait, la justification d'un refus ou d'une correction (elle est
  adressée à la personne concernée), et un lien ;
- personne n'est prévenu de sa propre action, et chacun peut couper ces
  e-mails depuis son espace (les badges de l'accueil restent).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from flask import current_app, url_for

from app.extensions import db
from app.utils.dates import utcnow

MAX_TENTATIVES = 5
LOT_ENVOI = 20
#: Un e-mail encore en file après ce délai n'est plus envoyé : « ta demande
#: du mois dernier est à transmettre » n'aide plus personne (ex. serveur de
#: mail configuré longtemps après la mise en service).
EXPIRATION_JOURS = 7

SIGNATURE = (
    "\n\n— \nMessage automatique de l'espace salarié. "
    "Pour ne plus recevoir ces e-mails : Accueil → Espace salarié → Mes e-mails."
)


# ---------------------------------------------------------------------------
# Destinataires
# ---------------------------------------------------------------------------

def courriels_actifs_pour(user) -> bool:
    """Préférence personnelle (actifs par défaut)."""
    from app.models import PreferenceCourrielRh
    pref = PreferenceCourrielRh.query.filter_by(user_id=user.id).first()
    return True if pref is None else bool(pref.actif)


def changer_preference(user, actif: bool) -> None:
    from app.models import PreferenceCourrielRh
    pref = PreferenceCourrielRh.query.filter_by(user_id=user.id).first()
    if pref is None:
        pref = PreferenceCourrielRh(user_id=user.id)
        db.session.add(pref)
    pref.actif = bool(actif)
    db.session.commit()


def comptes_avec(permission: str, *, sauf: Iterable[str] = ()) -> list:
    """Comptes actifs ayant la permission (et aucune des permissions ``sauf``)."""
    from app.models import User
    res = []
    for u in User.query.filter(User.actif.is_(True)).order_by(User.id).all():
        try:
            if u.has_perm(permission) and not any(u.has_perm(p) for p in sauf):
                res.append(u)
        except Exception:  # un compte mal configuré ne bloque pas les autres
            continue
    return res


def relais_recuperations() -> list:
    """Qui reçoit une demande signée : l'assistant·e (relais sans pouvoir de
    décision) ; à défaut d'assistant·e, la direction."""
    return comptes_avec("recup:transmettre", sauf=("recup:decider",)) or comptes_avec("recup:decider")


# ---------------------------------------------------------------------------
# Mise en file
# ---------------------------------------------------------------------------

def _lien(endpoint: str, **valeurs) -> str:
    """Lien absolu vers la page, sur l'adresse publique configurée si elle existe."""
    try:
        from app.services.public_urls import public_base_url
        return public_base_url() + url_for(endpoint, **valeurs)
    except Exception:
        return ""


def programmer(evenement: str, destinataires: Iterable, *, sujet: str, corps: str,
               objet_type: str | None = None, objet_id: int | None = None,
               auteur=None) -> int:
    """Écrit un e-mail par destinataire dans la file (sans commit).

    Ignore l'auteur de l'action, les comptes sans adresse, inactifs ou
    désinscrits, et les doublons. Renvoie le nombre d'e-mails mis en file.
    """
    from app.models import CourrielRh
    app_name = current_app.config.get("APP_NAME") or "Espace salarié"
    vus: set[int] = set()
    n = 0
    for user in destinataires:
        if user is None or user.id in vus:
            continue
        vus.add(user.id)
        if auteur is not None and user.id == getattr(auteur, "id", None):
            continue
        adresse = (getattr(user, "email", "") or "").strip()
        if "@" not in adresse or not getattr(user, "actif", True) or not courriels_actifs_pour(user):
            continue
        db.session.add(CourrielRh(
            user_id=user.id, destinataire=adresse, evenement=evenement,
            sujet=f"[{app_name}] {sujet}"[:255], corps=corps + SIGNATURE,
            objet_type=objet_type, objet_id=objet_id,
        ))
        n += 1
    return n


def expedier_en_attente(limite: int = LOT_ENVOI) -> dict:
    """Envoie les e-mails en file. Ne lève jamais : renvoie un bilan.

    Serveur de mail non configuré : rien n'est tenté (les e-mails attendent,
    sans consommer de tentative) — c'est l'état normal tant que
    l'administration n'a pas réglé le SMTP.
    """
    from app.models import CourrielRh
    from app.services.instance_settings import resolve_mail_settings
    from app.services.notifications import _envoyer_texte

    bilan = {"envoyes": 0, "echecs": 0, "smtp": True}
    try:
        cfg = resolve_mail_settings(current_app.config)
        if not cfg.get("host") or not cfg.get("sender"):
            bilan["smtp"] = False
            return bilan
        perimes = (CourrielRh.query
                   .filter(CourrielRh.envoye_le.is_(None), CourrielRh.tentatives < MAX_TENTATIVES,
                           CourrielRh.created_at < utcnow() - timedelta(days=EXPIRATION_JOURS))
                   .update({"tentatives": MAX_TENTATIVES,
                            "derniere_erreur": f"Expiré : non envoyé sous {EXPIRATION_JOURS} jours."},
                           synchronize_session=False))
        if perimes:
            db.session.commit()
        lot = (CourrielRh.query
               .filter(CourrielRh.envoye_le.is_(None), CourrielRh.tentatives < MAX_TENTATIVES)
               .order_by(CourrielRh.id.asc()).limit(limite).all())
        for courriel in lot:
            try:
                _envoyer_texte(courriel.destinataire, courriel.sujet, courriel.corps)
                courriel.envoye_le = utcnow()
                courriel.derniere_erreur = None
                bilan["envoyes"] += 1
            except Exception as exc:  # noqa: BLE001 — un échec n'arrête pas les suivants
                courriel.tentatives = (courriel.tentatives or 0) + 1
                courriel.derniere_erreur = str(exc)[:500]
                bilan["echecs"] += 1
            db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning("File des e-mails RH : envoi interrompu", exc_info=True)
    if bilan["echecs"]:
        current_app.logger.warning("File des e-mails RH : %s échec(s) d'envoi", bilan["echecs"])
    return bilan


def etat_file() -> dict:
    """Pour Administration → Notifications."""
    from app.models import CourrielRh
    depuis = utcnow() - timedelta(days=30)
    return {
        "en_attente": CourrielRh.query.filter(CourrielRh.envoye_le.is_(None),
                                              CourrielRh.tentatives < MAX_TENTATIVES).count(),
        "abandonnes": CourrielRh.query.filter(CourrielRh.envoye_le.is_(None),
                                              CourrielRh.tentatives >= MAX_TENTATIVES).count(),
        "envoyes_30j": CourrielRh.query.filter(CourrielRh.envoye_le >= depuis).count(),
        "derniers_echecs": (CourrielRh.query.filter(CourrielRh.derniere_erreur.isnot(None),
                                                    CourrielRh.envoye_le.is_(None))
                            .order_by(CourrielRh.id.desc()).limit(5).all()),
    }


def relancer_abandonnes() -> int:
    """Remet en file les e-mails abandonnés (après correction du SMTP)."""
    from app.models import CourrielRh
    n = (CourrielRh.query.filter(CourrielRh.envoye_le.is_(None), CourrielRh.tentatives >= MAX_TENTATIVES)
         .update({"tentatives": 0}, synchronize_session=False))
    db.session.commit()
    return n


# ---------------------------------------------------------------------------
# Les événements
# ---------------------------------------------------------------------------

def _quand(demande) -> str:
    from app.services.espace_salarie import format_minutes
    return f"du {demande.date_recuperation.strftime('%d/%m/%Y')} ({format_minutes(demande.minutes)})"


def demande_soumise(demande, auteur) -> int:
    nom = demande.salarie.nom_complet
    return programmer(
        "recup_soumise", relais_recuperations(), auteur=auteur,
        sujet=f"Demande de récupération de {nom} à transmettre",
        corps=(f"Bonjour,\n\n{nom} a signé une demande de récupération {_quand(demande)}.\n"
               f"Elle attend d'être transmise à la direction.\n\n"
               f"{_lien('salaries.equipe_recuperations', statut='soumise')}"),
        objet_type="demande_recuperation", objet_id=demande.id)


def demande_transmise(demande, auteur) -> int:
    """Transmise par l'assistant·e : la direction décide, le salarié est informé."""
    nom = demande.salarie.nom_complet
    n = programmer(
        "recup_transmise", comptes_avec("recup:decider"), auteur=auteur,
        sujet=f"Demande de récupération de {nom} à décider",
        corps=(f"Bonjour,\n\nLa demande de récupération de {nom} {_quand(demande)} "
               f"vous a été transmise et attend votre décision.\n\n"
               f"{_lien('salaries.equipe_recuperations', statut='transmise')}"),
        objet_type="demande_recuperation", objet_id=demande.id)
    n += programmer(
        "recup_transmise_salarie", [demande.salarie.compte], auteur=auteur,
        sujet="Ta demande de récupération est transmise à la direction",
        corps=(f"Bonjour,\n\nTa demande de récupération {_quand(demande)} a été transmise à la direction, "
               f"qui va l'accepter ou la refuser. Tu seras prévenu·e de sa décision.\n\n"
               f"{_lien('salaries.demande_detail', demande_id=demande.id)}"),
        objet_type="demande_recuperation", objet_id=demande.id)
    return n


def _justification(demande) -> str:
    return f"\nJustification : {demande.commentaire_direction}\n" if demande.commentaire_direction else ""


def demande_refusee_relais(demande, auteur) -> int:
    """Refus au premier niveau (assistant·e) : le salarié est informé, justification comprise."""
    return programmer(
        "recup_refusee_relais", [demande.salarie.compte], auteur=auteur,
        sujet="Ta demande de récupération est refusée",
        corps=(f"Bonjour,\n\nTa demande de récupération {_quand(demande)} a été refusée par "
               f"{getattr(auteur, 'nom', 'l’assistant·e de direction')} (assistant·e de direction).\n"
               f"{_justification(demande)}\n{_lien('salaries.demande_detail', demande_id=demande.id)}"),
        objet_type="demande_recuperation", objet_id=demande.id)


def demande_decidee(demande, auteur) -> int:
    """Décision de la direction : le salarié et l'assistant·e sont prévenus."""
    decision = "acceptée" if demande.statut == "acceptee" else "refusée"
    nom = demande.salarie.nom_complet
    n = programmer(
        "recup_decidee", [demande.salarie.compte], auteur=auteur,
        sujet=f"Ta demande de récupération est {decision}",
        corps=(f"Bonjour,\n\nTa demande de récupération {_quand(demande)} a été {decision} "
               f"par la direction.\n{_justification(demande)}\n"
               f"{_lien('salaries.demande_detail', demande_id=demande.id)}"),
        objet_type="demande_recuperation", objet_id=demande.id)
    n += programmer(
        "recup_decidee_relais", relais_recuperations(), auteur=auteur,
        sujet=f"Décision sur la demande de récupération de {nom} : {decision}",
        corps=(f"Bonjour,\n\nLa direction a {decision.replace('ée', 'é')} la demande de récupération de {nom} "
               f"{_quand(demande)}. Le salarié a été prévenu ; il te reste à en prendre connaissance.\n\n"
               f"{_lien('salaries.equipe_recuperations', statut='a_traiter')}"),
        objet_type="demande_recuperation", objet_id=demande.id)
    return n


def heures_corrigees(correction, avant: int, apres: int, auteur) -> int:
    """Déclaration corrigée (dans un sens ou dans l'autre) ou retirée : le salarié est informé."""
    from app.services.espace_salarie import format_minutes
    jour = correction.date_travail.strftime("%d/%m/%Y")
    quoi = (f"retirées ({format_minutes(avant)} déclarées)" if apres == 0
            else f"corrigées : {format_minutes(avant)} → {format_minutes(apres)}")
    return programmer(
        "heures_corrigees", [correction.salarie.compte], auteur=auteur,
        sujet="Tes heures supplémentaires ont été " + ("retirées" if apres == 0 else "corrigées"),
        corps=(f"Bonjour,\n\nTes heures supplémentaires du {jour} ont été {quoi} par "
               f"{getattr(auteur, 'nom', 'la direction')}.\n"
               f"Justification : {correction.commentaire_direction}\n\n{_lien('salaries.mes_recuperations')}"),
        objet_type="heure_supplementaire", objet_id=correction.id)


def document_depose(document, auteur) -> int:
    from app.models import User
    lecteurs = User.query.filter(User.id.in_([a.user_id for a in document.acces])).all() if document.acces else []
    return programmer(
        "document_depose", lecteurs, auteur=auteur,
        sujet=f"Un document t'a été partagé : {document.type_document.libelle}",
        corps=(f"Bonjour,\n\n{getattr(auteur, 'nom', 'Quelqu’un')} a déposé un document "
               f"(« {document.type_document.libelle} ») dans le coffre-fort et te l'a partagé.\n"
               f"Il se consulte uniquement dans l'application.\n\n"
               f"{_lien('salaries.documents')}"),
        objet_type="document_rh", objet_id=document.id)
