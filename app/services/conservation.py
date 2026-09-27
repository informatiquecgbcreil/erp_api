"""Durées de conservation hors fiches participants (audit 3.2 et 3.8).

Chaque règle est réglable depuis Contrôle → Purge RGPD (0 = conserver sans
limite) et n'est appliquée que par la maintenance quotidienne, quand la
purge automatique est activée (désactivée par défaut), ou par le bouton
« Appliquer maintenant ». Rien d'irréversible ne démarre seul sur une
installation neuve ou juste reprise.

Valeurs par défaut :
- journal d'audit : 3 ans (1 095 jours) ;
- bulletins d'inscription jamais rattachés à une fiche : 3 ans ;
- donateurs : 6 ans après l'année du don (durée de contrôle fiscal des
  reçus) — le montant, la date et le numéro du reçu restent ;
- comptes désactivés : 3 ans après la dernière connexion réussie ;
- lignes d'import historique de personnes non importées : 1 an (seule la
  ligne source brute est réduite, la décision prise reste).
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta

from app.extensions import db
from app.utils.dates import utcnow

REGLES = {
    # nom du réglage : (défaut, maximum, unité, libellé)
    "conservation_journal_jours": (1095, 3650, "jours", "Journal d'audit"),
    "conservation_bulletins_annees": (3, 10, "ans", "Bulletins jamais rattachés à une fiche"),
    "conservation_donateurs_annees": (6, 10, "ans", "Identité des donateurs (après l'année du don)"),
    "conservation_comptes_annees": (3, 10, "ans", "Comptes désactivés (après la dernière connexion)"),
    "conservation_imports_annees": (1, 10, "ans", "Lignes d'import de personnes non importées"),
}


def reglage(nom: str) -> int:
    """Valeur en vigueur (réglage de l'instance, sinon défaut). 0 = sans limite."""
    from app.models import InstanceSettings
    defaut = REGLES[nom][0]
    try:
        ligne = InstanceSettings.query.first()
    except Exception:  # noqa: BLE001 — table pas encore migrée
        db.session.rollback()
        return defaut
    valeur = getattr(ligne, nom, None) if ligne else None
    return defaut if valeur is None else max(0, min(int(valeur), REGLES[nom][1]))


def reglages() -> dict[str, int]:
    return {nom: reglage(nom) for nom in REGLES}


def _il_y_a(annees: int) -> datetime:
    return utcnow() - timedelta(days=365 * annees)


# ---------------------------------------------------------------------------
# Règles
# ---------------------------------------------------------------------------

def purger_journal(jours: int) -> int:
    from app.models import AuditLog
    if not jours:
        return 0
    return AuditLog.query.filter(AuditLog.created_at < utcnow() - timedelta(days=jours)).delete(
        synchronize_session=False)


def anonymiser_bulletins_isoles(annees: int) -> int:
    """Bulletins sans fiche (demande jamais aboutie) : l'identité part, la
    demande (ateliers souhaités, créneaux) reste pour les bilans de campagne."""
    from app.models import Encaissement, InscriptionAnnuelle
    from app.services.purge_rgpd import NOM_ANONYME
    if not annees:
        return 0
    seuil = _il_y_a(annees)
    nombre = 0
    candidats = InscriptionAnnuelle.query.filter(
        InscriptionAnnuelle.participant_id.is_(None), InscriptionAnnuelle.nom != NOM_ANONYME).all()
    for bulletin in candidats:
        dates = [d for d in (bulletin.updated_at, bulletin.created_at,
                             datetime.combine(bulletin.date_inscription, datetime.min.time())
                             if isinstance(bulletin.date_inscription, date) else None) if d]
        if not dates or max(dates) >= seuil:
            continue
        for champ in ("adresse", "code_postal", "ville", "email", "telephone", "date_naissance",
                      "commentaire", "reglement_commentaire", "ateliers_libre", "benevolat_mission"):
            if hasattr(bulletin, champ):
                setattr(bulletin, champ, None)
        bulletin.nom = NOM_ANONYME
        bulletin.prenom = f"B{bulletin.id}"
        for membre in bulletin.membres:
            if membre.participant_id is None:
                membre.nom, membre.prenom = NOM_ANONYME, f"M{membre.id}"
                membre.date_naissance = None
                membre.lien_filiation = None
        for encaissement in Encaissement.query.filter_by(inscription_annuelle_id=bulletin.id).all():
            encaissement.commentaire = None
        nombre += 1
    return nombre


def anonymiser_donateurs(annees: int) -> int:
    from app.models import Don
    if not annees:
        return 0
    derniere_annee = date.today().year - annees - 1
    nombre = 0
    for don in Don.query.filter(Don.annee <= derniere_annee, Don.donateur_nom != "Donateur anonymisé").all():
        don.donateur_nom = "Donateur anonymisé"
        don.donateur_civilite = don.donateur_prenom = None
        don.donateur_adresse = don.donateur_cp = don.donateur_ville = don.donateur_email = None
        nombre += 1
    return nombre


def anonymiser_comptes_desactives(annees: int) -> int:
    from app.models import AuditLog, JournalConnexion, User
    if not annees:
        return 0
    seuil = _il_y_a(annees)
    nombre = 0
    for user in User.query.filter(User.actif.is_(False), ~User.email.like("compte-%@supprime.invalid")).all():
        derniere = (db.session.query(db.func.max(JournalConnexion.cree_le))
                    .filter(JournalConnexion.email == user.email, JournalConnexion.succes.is_(True)).scalar())
        reference = max(d for d in (derniere, user.created_at, datetime.min) if d)
        if reference >= seuil:
            continue
        ancien = user.email
        user.email = f"compte-{user.id}@supprime.invalid"
        user.nom = f"Compte supprimé #{user.id}"
        user.set_password(secrets.token_urlsafe(32))
        if hasattr(user, "calendar_token"):
            user.calendar_token = None
        AuditLog.query.filter(AuditLog.user_id == user.id).update(
            {"user_email": user.email}, synchronize_session=False)
        JournalConnexion.query.filter(JournalConnexion.email == ancien).delete(synchronize_session=False)
        nombre += 1
    return nombre


def reduire_imports(annees: int) -> int:
    from app.models import HistoricalImportBatch, HistoricalImportSource
    if not annees:
        return 0
    seuil = _il_y_a(annees)
    reduit = json.dumps({"reduit": "conservation"})
    lignes = (HistoricalImportSource.query
              .join(HistoricalImportBatch, HistoricalImportBatch.id == HistoricalImportSource.batch_id)
              .filter(HistoricalImportSource.kind == "participant",
                      HistoricalImportSource.participant_id.is_(None),
                      HistoricalImportSource.raw_json != reduit,
                      HistoricalImportBatch.created_at < seuil).all())
    for ligne in lignes:
        ligne.raw_json = reduit
    return len(lignes)


def appliquer(declenchement: str = "manuel") -> dict:
    """Applique toutes les règles, chacune dans son point de sauvegarde (une
    règle en erreur n'empêche pas les autres). Commit inclus."""
    from flask import current_app
    from app.services.audit import enregistrer
    valeurs = reglages()
    etapes = {
        "bulletins": (anonymiser_bulletins_isoles, "conservation_bulletins_annees"),
        "donateurs": (anonymiser_donateurs, "conservation_donateurs_annees"),
        "comptes": (anonymiser_comptes_desactives, "conservation_comptes_annees"),
        "imports": (reduire_imports, "conservation_imports_annees"),
        "journal": (purger_journal, "conservation_journal_jours"),
    }
    rapport = {}
    for nom, (fonction, cle) in etapes.items():
        try:
            with db.session.begin_nested():
                rapport[nom] = fonction(valeurs[cle])
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Conservation : règle « %s » non appliquée", nom)
            rapport[nom] = "erreur"
    if any(isinstance(v, int) and v for v in rapport.values()):
        enregistrer("rgpd.conservation", cible=declenchement[:200], details=rapport)
    db.session.commit()
    return rapport
