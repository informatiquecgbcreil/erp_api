"""Mode entraînement : se former sur de fausses données, puis passer au réel.

Un centre qui découvre l'application n'ose pas cliquer : il a peur de
« casser » ses vraies données. Le mode entraînement remplit une base
**vierge** de données d'exemple (habitants, activités, séances, présences,
adhésions réglées en caisse) et affiche un bandeau orange sur toutes les
pages. L'équipe s'entraîne, crée, modifie, supprime à volonté. Puis
« Tout effacer et passer en mode réel » remet la base au propre.

Comment l'effacement reste sûr :

- **Base vierge seulement** : l'activation est refusée dès qu'il existe une
  vraie donnée d'activité (un habitant, une activité, un don…). Fausses et
  vraies données ne se mélangent jamais.
- **Photo** : à l'activation, on note le plus grand numéro de chaque table.
  En quittant, on efface ce qui a été créé depuis — données d'exemple ET
  saisies d'entraînement — et rien de ce qui existait avant.
- **Ce qu'on garde** (décision du centre) : comptes, réglages (identité,
  territoire, modules), secteurs, quartiers, salles, tarifs et référentiels,
  même créés pendant l'entraînement. Ce sont des choix de configuration,
  pas des données d'exemple — le jeu d'exemple n'en crée d'ailleurs aucun.
- **Sauvegarde** complète juste avant d'effacer ; si elle échoue, rien
  n'est effacé.
- **Reçus fiscaux et factures** ne s'émettent pas pendant l'entraînement :
  leur numérotation officielle ne recule jamais (voir financial_sequence),
  un faux reçu n° 1 ferait commencer les vrais au n° 2.
"""
from __future__ import annotations

import json

from app.extensions import db
from app.services.registre_externe import RegistreErreur
from app.utils.dates import utcnow

#: Tables gardées entièrement en quittant l'entraînement : comptes,
#: réglages, lieux, référentiels, et les traces qu'on ne réécrit jamais.
TABLES_GARDEES = frozenset({
    # Comptes et sécurité.
    "user", "role", "permission", "role_permissions", "user_roles", "user_dashboard_preference",
    "agenda_preference", "notification_reglage", "preference_courriel_rh", "journal_connexion",
    "session_revoquee", "google_agenda_compte",
    # Réglages et traces.
    "instance_settings", "financial_sequence", "portail_sync_state", "tache_planifiee",
    "pending_file_deletion", "journal_audit", "effacement_rgpd",
    # Lieux.
    "secteur", "quartier", "site", "espace", "tarif_salle", "tarif_bareme", "majoration_salle",
    "categorie_preneur", "materiel_type", "materiel_consommation_config",
    # Référentiels.
    "framework", "skill", "competence", "referentiel", "glossaire_terme", "insertion_diplome_ref",
    "insertion_dispositif_ref", "insertion_niveau_ref", "insertion_prescripteur_ref",
    "insertion_titre_sejour_type_ref", "bareme_kilometrique", "budget_compte_referentiel",
    "budget_modele_referentiel", "budget_modele_ligne_referentiel", "budget_categorie_referentiel",
    "type_document_rh", "transition_thematique", "pedagogie_module", "veille_source",
    "objectif_sectoriel", "portail_competence_map", "module_competence",
})

#: Une seule ligne dans l'une de ces tables, et la base n'est plus vierge.
TEMOINS_ACTIVITE = {
    "participant": "des habitants",
    "atelier_activite": "des activités",
    "session_activite": "des séances",
    "presence_activite": "des présences",
    "inscription_annuelle": "des inscriptions annuelles",
    "cotisation": "des adhésions",
    "encaissement": "des règlements",
    "caisse_mouvement": "des mouvements de caisse",
    "don": "des dons",
    "depense": "des dépenses",
    "subvention": "des subventions",
    "projet": "des projets",
    "reservation": "des réservations de salle",
    "salarie": "des salariés",
}

_CLE_CACHE = "mcs_entrainement"


class EmissionEnEntrainement(RegistreErreur):
    """Un reçu fiscal ou une facture demandé pendant l'entraînement."""

    def __init__(self):
        super().__init__(
            "Mode entraînement : les reçus fiscaux et les factures ne s'émettent pas sur des données "
            "d'exemple, car leur numérotation officielle ne recule jamais. Passez en mode réel pour en émettre.")


class EntrainementImpossible(ValueError):
    """Activation ou sortie refusée ; le message s'adresse à l'utilisateur."""


def _reglages():
    from app.models import InstanceSettings

    return InstanceSettings.query.first()


def actif() -> bool:
    """Le mode entraînement est-il en cours ? Lu une fois par requête."""
    from flask import g, has_app_context

    if has_app_context():
        deja = g.get(_CLE_CACHE)
        if deja is not None:
            return deja
    reglages = _reglages()
    etat = bool(reglages and reglages.entrainement_actif)
    if has_app_context():
        setattr(g, _CLE_CACHE, etat)
    return etat


def oublier() -> None:
    from flask import g, has_app_context

    if has_app_context():
        g.pop(_CLE_CACHE, None)


def interdire_emission() -> None:
    """Appelé avant d'attribuer un numéro de reçu fiscal ou de facture."""
    if actif():
        raise EmissionEnEntrainement()


def donnees_reelles() -> list[str]:
    """Ce qui empêche d'activer l'entraînement (« des habitants », …) ; vide = base vierge."""
    import sqlalchemy as sa

    presentes = []
    tables = db.metadata.tables
    for nom, libelle in TEMOINS_ACTIVITE.items():
        table = tables.get(nom)
        if table is not None and db.session.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
            presentes.append(libelle)
    return presentes


def _a_un_id_entier(table) -> bool:
    import sqlalchemy as sa

    colonne = table.c.get("id")
    return colonne is not None and colonne.primary_key and isinstance(colonne.type, sa.Integer)


def photo() -> dict[str, int]:
    """Le plus grand numéro de chaque table (0 si vide)."""
    import sqlalchemy as sa

    return {
        table.name: int(db.session.execute(sa.select(sa.func.max(table.c.id))).scalar() or 0)
        for table in db.metadata.sorted_tables if _a_un_id_entier(table)
    }


def activer(auteur_id: int | None = None) -> dict:
    """Passe en mode entraînement et crée les données d'exemple. Valide la transaction."""
    from app.models import InstanceSettings
    from app.services.audit import journaliser
    from app.services.donnees_exemple import creer as creer_donnees_exemple

    if actif():
        raise EntrainementImpossible("Le mode entraînement est déjà en cours.")
    presentes = donnees_reelles()
    if presentes:
        raise EntrainementImpossible(
            "Le mode entraînement ne s'active que sur une base sans données réelles, pour ne jamais mélanger "
            f"vraies et fausses données. Cette base contient déjà {', '.join(presentes)}.")

    reglages = _reglages()
    if reglages is None:
        reglages = InstanceSettings()
        db.session.add(reglages)
    reglages.entrainement_photo = json.dumps(photo())
    reglages.entrainement_depuis = utcnow()
    reglages.entrainement_actif = True
    db.session.flush()
    resume = creer_donnees_exemple(auteur_id)
    db.session.commit()
    oublier()
    journaliser("entrainement.active", cible="structure", details=resume)
    return resume


def _references_vers_effacees(photo_tables: dict[str, int], effacees: set[str]):
    """(table, colonne, table visée) pour chaque lien vers une table effacée."""
    for table in db.metadata.tables.values():
        for cle in table.foreign_keys:
            visee = cle.column.table
            # Seules les tables numérotées ont une photo : un lot de reprise
            # historique, identifié par un texte, s'efface par sa date.
            if visee.name in effacees and cle.column.name == "id" and _a_un_id_entier(visee):
                yield table, cle.parent, visee.name


def _effacer(photo_tables: dict[str, int], depuis) -> dict[str, int]:
    """Efface ce qui a été créé depuis la photo, hors tables gardées. Ne valide pas."""
    import sqlalchemy as sa

    effacees = {t.name for t in db.metadata.sorted_tables if t.name not in TABLES_GARDEES}
    seuil = lambda nom: photo_tables.get(nom, 0)  # noqa: E731

    # 1) Une ligne conservée (antérieure à la photo) qui pointe vers une ligne
    # à effacer : le lien est coupé s'il peut l'être, sinon on s'arrête.
    for table, colonne, visee in _references_vers_effacees(photo_tables, effacees):
        if table.name in TABLES_GARDEES:
            conservees = sa.true()
        elif _a_un_id_entier(table):
            conservees = table.c.id <= seuil(table.name)
        else:
            continue
        vers_nouvelle = sa.and_(conservees, colonne > seuil(visee))
        if not db.session.execute(sa.select(sa.literal(1)).select_from(table).where(vers_nouvelle).limit(1)).first():
            continue
        if not colonne.nullable:
            raise EntrainementImpossible(
                f"Effacement impossible : des données à garder ({table.name}) dépendent de données "
                "d'entraînement. Rien n'a été effacé ; générez un rapport SOS.")
        db.session.execute(table.update().where(vers_nouvelle).values({colonne.name: None}))

    # 2) Les lignes créées depuis la photo, des tables « enfants » vers les
    # tables « parents » pour respecter les liens.
    comptes: dict[str, int] = {}
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in effacees:
            continue
        if _a_un_id_entier(table):
            condition = table.c.id > seuil(table.name)
        else:
            # Tables d'association : les lignes qui relient une donnée effacée.
            liens = [cle.parent > seuil(cle.column.table.name) for cle in table.foreign_keys
                     if cle.column.table.name in effacees and cle.column.name == "id"
                     and _a_un_id_entier(cle.column.table)]
            if "created_at" in table.c and depuis is not None:
                liens.append(table.c.created_at >= depuis)
            if not liens:
                continue
            condition = sa.or_(*liens)
        resultat = db.session.execute(table.delete().where(condition))
        if resultat.rowcount:
            comptes[table.name] = resultat.rowcount
    return comptes


def quitter(auteur_id: int | None = None) -> dict:
    """Sauvegarde, puis efface tout ce qui a été créé pendant l'entraînement.

    Lève ``EntrainementImpossible`` (rien n'est effacé) si le mode n'est pas
    actif, si la sauvegarde échoue ou si l'effacement rencontre un lien
    qu'il ne sait pas couper.
    """
    from app.services.audit import journaliser
    from app.services.sauvegarde import creer_sauvegarde

    reglages = _reglages()
    if reglages is None or not reglages.entrainement_actif:
        raise EntrainementImpossible("Le mode entraînement n'est pas en cours.")
    try:
        photo_tables = json.loads(reglages.entrainement_photo or "{}")
    except ValueError:
        raise EntrainementImpossible("La photo de départ de l'entraînement est illisible : rien n'a été effacé. "
                                     "Générez un rapport SOS.") from None
    try:
        sauvegarde = creer_sauvegarde()["base"]
    except Exception as exc:  # noqa: BLE001 — pas d'effacement sans filet
        raise EntrainementImpossible(
            f"La sauvegarde de sécurité a échoué ({exc}) : rien n'a été effacé. "
            "Vérifiez Administration > Sauvegardes, puis recommencez.") from exc

    try:
        comptes = _effacer(photo_tables, reglages.entrainement_depuis)
        reglages.entrainement_actif = False
        reglages.entrainement_photo = None
        reglages.entrainement_depuis = None
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    oublier()
    resume = {"sauvegarde": sauvegarde, "lignes_effacees": sum(comptes.values()), "tables": comptes}
    journaliser("entrainement.quitte", cible="structure", details=resume)
    return resume
