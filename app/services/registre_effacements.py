"""Registre des effacements RGPD, protégé contre les restaurations.

Problème : restaurer une sauvegarde d'avant une anonymisation (ou une
suppression) faisait revenir la personne. Un registre hors base permet de
réappliquer l'effacement après la restauration.

Défaut corrigé (consolidation après la PR #59) : l'ancien registre était
écrit IMMÉDIATEMENT, avant la validation SQL. Un effacement annulé (rollback,
échec d'une fiche dans une purge par lots) restait noté, et la restauration
suivante l'appliquait pour de bon.

Protocole actuel, en deux temps :

1. **Dans la transaction de l'effacement**, une ligne ``effacement_rgpd``
   (état ``confirme``, ``exporte_le`` vide) est ajoutée. Base et fichier ne
   forment pas une transaction atomique ; la base, elle, en est une : la
   ligne n'existe que si l'effacement est validé. Un rollback complet ou d'un
   point de sauvegarde l'emporte avec lui ; la validation d'un point de
   sauvegarde n'écrit rien de définitif tant que la transaction extérieure
   n'est pas validée.
2. **Après la validation**, les lignes non encore exportées sont recopiées
   dans le fichier ``runtime/registre-effacements.json`` (verrou, écriture
   atomique, voir ``registre_externe``), puis marquées exportées. Cette
   recopie est rejouée tant qu'elle n'a pas abouti : juste après chaque
   validation, à chaque démarrage, à chaque maintenance quotidienne et avant
   toute restauration.

Fenêtre de panne et garanties :

- arrêt entre la validation SQL et la recopie : la ligne reste « en
  attente » en base ; la recopie se fait au démarrage suivant ;
- arrêt entre l'écriture du fichier et le marquage : la recopie suivante
  réécrit la même entrée (fusion par clé, sans effet supplémentaire) ;
- échec d'écriture (droits, disque) : rien n'est masqué ; l'effacement est
  fait, sa protection hors base est « en attente », affichée dans Contrôle ;
- **restauration demandée alors qu'une recopie est en attente** : la
  restauration recopie d'abord ; si c'est impossible, elle est refusée
  (la ligne en base serait la seule trace, et la restauration l'écraserait).

Limite : une restauration faite hors de l'application (outil en ligne de
commande, restauration manuelle) n'appelle pas cette recopie préalable. Les
effacements validés mais pas encore recopiés au moment de cette restauration
externe seraient perdus pour le registre ; c'est pourquoi la recopie suit
immédiatement la validation et que le démarrage réapplique le registre.

Identification d'une personne : numéro de fiche ET date de création exacte
de la fiche (à la microseconde). Un numéro seul peut être réattribué à une
autre personne (restauration d'une base PostgreSQL : la séquence recule ;
SQLite réutilise le plus grand numéro supprimé). Une fiche ancienne sans date
de création n'est reconnue que si la fiche revenue n'en a pas non plus : les
fiches créées par l'application en ont toujours une.

Ancien registre (``runtime/effacements.json``, écrit avant validation) : il
est lu, jamais modifié (une version précédente de l'application, en cas de
retour arrière, continue de s'en servir). Ses entrées sont classées :
- fiche absente, ou présente et déjà anonymisée : effacement cohérent avec
  la base → ``confirme`` ;
- fiche présente, même date de création, identité intacte : l'effacement a
  pu être annulé → ``a_verifier``, jamais réappliqué automatiquement ;
  une personne habilitée tranche depuis Contrôle → Registres.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path

from flask import current_app, has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.extensions import db
from app.services import registre_externe as fichier_registre
from app.services.registre_externe import RegistreEndommage, RegistreErreur
from app.utils.dates import utcnow

journal = logging.getLogger(__name__)

NOM_FICHIER = "registre-effacements.json"
NOM_ANCIEN_FICHIER = "effacements.json"
FORMAT = 2

#: Anciennes catégories (API des appelants) -> nature enregistrée.
NATURES = {"anonymises": "anonymisation", "supprimes": "suppression"}
CATEGORIES = tuple(NATURES)
ETATS_FINAUX = {"confirme", "ecarte"}


class EffacementEnAttente(RegistreErreur):
    """Des effacements validés n'ont pas pu être recopiés hors base."""


def chemin() -> Path | None:
    dossier = fichier_registre.dossier_runtime()
    return dossier / NOM_FICHIER if dossier else None


def chemin_ancien() -> Path | None:
    dossier = fichier_registre.dossier_runtime()
    return dossier / NOM_ANCIEN_FICHIER if dossier else None


# ---------------------------------------------------------------------------
# Dates et entrées
# ---------------------------------------------------------------------------

def _date_iso(valeur: datetime | None) -> str | None:
    return valeur.isoformat(timespec="microseconds") if valeur else None


def _date_lue(valeur) -> datetime | None:
    if not valeur:
        return None
    if isinstance(valeur, datetime):
        return valeur
    return datetime.fromisoformat(str(valeur))


def _entree(ligne) -> dict:
    return {
        "nature": ligne.nature,
        "participant_id": int(ligne.participant_id),
        "participant_cree_le": _date_iso(ligne.participant_cree_le),
        "precision": ligne.precision,
        "etat": ligne.etat,
        "origine": ligne.origine,
        "cree_le": _date_iso(ligne.cree_le),
        "decide_le": _date_iso(ligne.decide_le),
        "note": ligne.note,
    }


def _gagnante(a: dict | None, b: dict | None) -> dict | None:
    """Fusion de deux versions d'une même entrée : une décision (confirmé ou
    écarté) l'emporte sur « à vérifier » ; entre deux décisions, la plus
    récente. Jamais de retour à « à vérifier »."""
    if a is None or b is None:
        return a or b
    if (a["etat"] in ETATS_FINAUX) != (b["etat"] in ETATS_FINAUX):
        return a if a["etat"] in ETATS_FINAUX else b
    if a["etat"] != b["etat"]:
        return a if (a.get("decide_le") or "") >= (b.get("decide_le") or "") else b
    return a


def _valider(donnees: dict | None) -> dict:
    if donnees is None:
        return {"format": FORMAT, "entrees": {}}
    entrees = donnees.get("entrees")
    if donnees.get("format") != FORMAT or not isinstance(entrees, dict):
        raise RegistreEndommage("Le registre des effacements est endommagé.", "format inattendu")
    for cle, entree in entrees.items():
        if (not isinstance(entree, dict) or entree.get("nature") not in NATURES.values()
                or not isinstance(entree.get("participant_id"), int)
                or entree.get("etat") not in {"confirme", "a_verifier", "ecarte"}):
            raise RegistreEndommage("Le registre des effacements est endommagé.", "entrée inattendue")
    return donnees


NOM_INCIDENT = "registre-effacements.incident.json"


def _reconstituer(fichier: Path, raison: str, detail: str, mis_de_cote: Path | None) -> dict:
    """Reconstruction depuis la copie précédente et la base. Ce n'est PAS une
    garantie complète (une entrée présente seulement dans le fichier perdu
    manquerait) : un incident persistant est ouvert, affiché dans Contrôle →
    Registres jusqu'à ce qu'une personne habilitée l'ait vérifié."""
    from app.models import EffacementRgpd
    from app.utils.dates import utcnow as _maintenant
    try:
        copie = _valider(fichier_registre.lire(fichier.with_name(fichier.name + ".prec")))
    except RegistreEndommage:
        copie = _valider(None)
    # Connexion propre : appelé aussi après une validation (after_commit), où
    # la session ne peut plus émettre de requête.
    table = EffacementRgpd.__table__
    with db.engine.connect() as connexion:
        for ligne in connexion.execute(table.select()):
            copie["entrees"][ligne.cle] = _gagnante(copie["entrees"].get(ligne.cle), _entree(ligne))
    fichier_registre.ecrire(fichier, copie)
    fichier_registre.ecrire(fichier.with_name(NOM_INCIDENT), {
        "raison": raison, "detail": detail, "depuis": _maintenant().isoformat(timespec="seconds"),
        "mis_de_cote": mis_de_cote.name if mis_de_cote else None, "entrees_reconstituees": len(copie["entrees"])})
    journal.error("Registre des effacements %s (%s) : reconstitué depuis la copie précédente et la base ; "
                  "incident ouvert (Contrôle → Registres).", raison, detail)
    return copie


def _lire_fichier(fichier: Path) -> dict:
    """Sous verrou. Endommagé, ou disparu alors que des effacements avaient
    déjà été recopiés : fichier conservé, reconstitution depuis la copie
    précédente et la base, incident ouvert jusqu'à vérification."""
    try:
        donnees = fichier_registre.lire(fichier)
    except RegistreEndommage as exc:
        mis_de_cote = fichier_registre.mettre_de_cote(fichier)
        return _reconstituer(fichier, "endommagé", exc.detail, mis_de_cote)
    if donnees is None:
        from app.models import EffacementRgpd
        table = EffacementRgpd.__table__
        try:
            with db.engine.connect() as connexion:
                deja_recopies = connexion.execute(
                    table.select().where(table.c.exporte_le.isnot(None)).limit(1)).first() is not None
        except Exception:  # noqa: BLE001 — table absente
            deja_recopies = False
        if deja_recopies or fichier.with_name(fichier.name + ".prec").exists():
            return _reconstituer(fichier, "disparu", "fichier absent alors que des effacements y avaient été recopiés",
                                 None)
    try:
        return _valider(donnees)
    except RegistreEndommage as exc:
        mis_de_cote = fichier_registre.mettre_de_cote(fichier)
        return _reconstituer(fichier, "endommagé", exc.detail, mis_de_cote)


def incident() -> dict | None:
    fichier = chemin()
    if fichier is None:
        return None
    try:
        return fichier_registre.lire(fichier.with_name(NOM_INCIDENT))
    except RegistreEndommage:
        return {"raison": "inconnu", "detail": "marque d'incident illisible"}


def clore_incident(note: str) -> dict | None:
    """Une personne habilitée a vérifié la reconstruction (demandes
    d'effacement, journal) : l'incident est clos, la note journalisée."""
    fichier = chemin()
    if fichier is None:
        return None
    with fichier_registre.verrou(fichier):
        ouvert = incident()
        fichier.with_name(NOM_INCIDENT).unlink(missing_ok=True)
    from app.services.audit import enregistrer
    enregistrer("registres.effacements_incident_clos", details={"incident": ouvert, "note": (note or "")[:255]})
    db.session.commit()
    return ouvert


def fusionner_dans_fichier(entrees: dict[str, dict]) -> int:
    """Fusionne des entrées dans le registre hors base (sous verrou)."""
    fichier = chemin()
    if fichier is None or not entrees:
        return 0
    with fichier_registre.verrou(fichier):
        fichier_registre.nettoyer_temporaires(fichier)
        registre = _lire_fichier(fichier)
        avant = dict(registre["entrees"])
        for cle, entree in entrees.items():
            registre["entrees"][cle] = _gagnante(registre["entrees"].get(cle), entree)
        if registre["entrees"] != avant or not fichier.exists():
            fichier_registre.ecrire(fichier, registre)
    return len(entrees)


def lire_fichier() -> dict[str, dict]:
    """Entrées du registre hors base ({} s'il n'est pas tenu)."""
    fichier = chemin()
    if fichier is None:
        return {}
    with fichier_registre.verrou(fichier):
        fichier_registre.nettoyer_temporaires(fichier)
        return dict(_lire_fichier(fichier)["entrees"])


def lire() -> dict[str, list[dict]]:
    """Compatibilité : effacements confirmés du registre hors base, par
    catégorie ({"anonymises": [{"id", "cree_le"}], "supprimes": [...]})."""
    resultat: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    inverse = {v: k for k, v in NATURES.items()}
    for entree in lire_fichier().values():
        if entree["etat"] == "confirme":
            resultat[inverse[entree["nature"]]].append(
                {"id": entree["participant_id"], "cree_le": entree["participant_cree_le"]})
    return resultat


# ---------------------------------------------------------------------------
# 1. Dans la transaction de l'effacement
# ---------------------------------------------------------------------------

def noter(categorie: str, participant) -> None:
    """Note l'effacement dans la transaction en cours (rien hors base ici)."""
    from app.models import EffacementRgpd
    nature = NATURES.get(categorie)
    if nature is None or participant is None or participant.id is None:
        return
    cree = getattr(participant, "created_at", None)
    session = db.session()
    for objet in session.new:
        if (isinstance(objet, EffacementRgpd) and objet.participant_id == participant.id
                and objet.nature == nature and objet.participant_cree_le == cree):
            return
    with session.no_autoflush:
        deja = EffacementRgpd.query.filter(
            EffacementRgpd.participant_id == int(participant.id), EffacementRgpd.nature == nature,
            EffacementRgpd.etat == "confirme",
            EffacementRgpd.participant_cree_le.is_(None) if cree is None
            else EffacementRgpd.participant_cree_le == cree).first()
    if deja is not None:
        return  # Réapplication après restauration : déjà enregistré.
    session.add(EffacementRgpd(cle=uuid.uuid4().hex, nature=nature, participant_id=int(participant.id),
                               participant_cree_le=cree, precision="microseconde", etat="confirme",
                               origine="application", cree_le=utcnow()))
    session.info["effacements_a_exporter"] = True


# ---------------------------------------------------------------------------
# 2. Après la validation : recopie hors base
# ---------------------------------------------------------------------------

def exporter_en_attente() -> int:
    """Recopie hors base les effacements validés pas encore exportés.
    Lève ``RegistreErreur`` si la recopie échoue (les lignes restent en
    attente, rien n'est perdu). Renvoie le nombre d'entrées recopiées."""
    from app.models import EffacementRgpd
    if chemin() is None:
        return 0
    table = EffacementRgpd.__table__
    with db.engine.connect() as connexion:
        lignes = list(connexion.execute(table.select().where(table.c.exporte_le.is_(None))))
    if not lignes:
        return 0
    fusionner_dans_fichier({ligne.cle: _entree(ligne) for ligne in lignes})
    moment = utcnow()
    with db.engine.begin() as connexion:
        for ligne in lignes:
            # Une décision prise entre-temps (état changé) sera recopiée au
            # passage suivant : on ne marque que la version recopiée.
            connexion.execute(table.update().where(table.c.id == ligne.id, table.c.etat == ligne.etat,
                                                   table.c.exporte_le.is_(None)).values(exporte_le=moment))
    return len(lignes)


def nombre_en_attente() -> int:
    from app.models import EffacementRgpd
    if chemin() is None:
        return 0
    return EffacementRgpd.query.filter(EffacementRgpd.exporte_le.is_(None)).count()


@event.listens_for(Session, "after_commit")
def _apres_validation(session):
    if session.info.pop("effacements_a_exporter", False) and has_app_context():
        try:
            exporter_en_attente()
        except Exception:  # noqa: BLE001 — l'effacement est validé ; la recopie sera rejouée
            current_app.logger.exception(
                "Registre des effacements : recopie hors base en attente (reprise au prochain démarrage, "
                "à la maintenance ou avant une restauration).")


@event.listens_for(Session, "after_soft_rollback")
def _apres_annulation(session, transaction_precedente):
    if transaction_precedente.parent is None:
        session.info.pop("effacements_a_exporter", None)


# ---------------------------------------------------------------------------
# Ancien registre (écrit avant validation)
# ---------------------------------------------------------------------------

def _meme_creation(fiche_cree_le: datetime | None, cree_le: datetime | None, precision: str) -> bool:
    if cree_le is None or fiche_cree_le is None:
        return cree_le is None and fiche_cree_le is None
    if precision == "seconde":
        return fiche_cree_le.replace(microsecond=0) == cree_le.replace(microsecond=0)
    return fiche_cree_le == cree_le


def _classer_ancienne_entree(categorie: str, entree: dict) -> dict | None:
    from app.models import Participant
    from app.services.purge_rgpd import NOM_ANONYME
    try:
        pid = int(entree["id"])
        cree_le = _date_lue(entree.get("cree_le"))
    except (KeyError, TypeError, ValueError):
        return None
    fiche = db.session.get(Participant, pid)
    if fiche is None or not _meme_creation(fiche.created_at, cree_le, "seconde"):
        etat, note = "confirme", "Ancien registre : fiche absente de la base, effacement cohérent."
    elif fiche.nom == NOM_ANONYME:
        etat, note = "confirme", "Ancien registre : fiche déjà anonymisée en base."
    else:
        etat, note = "a_verifier", ("Ancien registre : la fiche est présente avec son identité ; l'effacement "
                                    "a pu être annulé. À vérifier avant toute réapplication.")
    return {"nature": NATURES[categorie], "participant_id": pid, "participant_cree_le": _date_iso(cree_le),
            "precision": "seconde", "etat": etat, "origine": "ancien_registre",
            "cree_le": _date_iso(utcnow()), "decide_le": None, "note": note}


def importer_ancien_registre() -> int:
    """Classe les entrées de l'ancien fichier (jamais modifié) une seule fois
    par contenu. Clés déterministes : réimporter ne crée aucun doublon."""
    ancien, fichier = chemin_ancien(), chemin()
    if ancien is None or fichier is None or not ancien.exists():
        return 0
    try:
        brut = ancien.read_bytes()
    except OSError as exc:
        raise RegistreErreur("L'ancien registre des effacements ne peut pas être lu.",
                             f"{ancien.name} ({exc.__class__.__name__})") from exc
    empreinte = hashlib.sha256(brut).hexdigest()
    with fichier_registre.verrou(fichier):
        registre = _lire_fichier(fichier)
        if registre.get("ancien_registre_empreinte") == empreinte:
            return 0
    try:
        donnees = fichier_registre.lire(ancien) or {}
    except RegistreEndommage:
        journal.error("Ancien registre des effacements illisible : conservé tel quel, à examiner.")
        return 0
    entrees: dict[str, dict] = {}
    for categorie in CATEGORIES:
        for brute in donnees.get(categorie, []) if isinstance(donnees.get(categorie), list) else []:
            if not isinstance(brute, dict):
                continue
            classee = _classer_ancienne_entree(categorie, brute)
            if classee is not None:
                cle = f"v1-{categorie}-{classee['participant_id']}-{classee['participant_cree_le'] or 'sans-date'}"
                entrees[cle] = classee
    with fichier_registre.verrou(fichier):
        registre = _lire_fichier(fichier)
        for cle, entree in entrees.items():
            registre["entrees"][cle] = _gagnante(registre["entrees"].get(cle), entree)
        registre["ancien_registre_empreinte"] = empreinte
        fichier_registre.ecrire(fichier, registre)
    return len(entrees)


# ---------------------------------------------------------------------------
# Après restauration (et au démarrage)
# ---------------------------------------------------------------------------

def importer_fichier_en_base() -> int:
    """Recopie en base les entrées du registre hors base absentes de la base
    ou plus avancées (base restaurée plus ancienne). Commit inclus."""
    from app.models import EffacementRgpd
    entrees = lire_fichier()
    if not entrees:
        return 0
    existantes = {ligne.cle: ligne for ligne in EffacementRgpd.query.filter(
        EffacementRgpd.cle.in_(list(entrees))).all()} if len(entrees) < 900 else {
        ligne.cle: ligne for ligne in EffacementRgpd.query.all()}
    changees = 0
    for cle, entree in entrees.items():
        ligne = existantes.get(cle)
        if ligne is None:
            db.session.add(EffacementRgpd(
                cle=cle, nature=entree["nature"], participant_id=entree["participant_id"],
                participant_cree_le=_date_lue(entree.get("participant_cree_le")),
                precision=entree.get("precision") or "microseconde", etat=entree["etat"],
                origine=entree.get("origine") or "registre_externe",
                cree_le=_date_lue(entree.get("cree_le")) or utcnow(), exporte_le=utcnow(),
                decide_le=_date_lue(entree.get("decide_le")), note=(entree.get("note") or None)))
            changees += 1
            continue
        actuelle = _entree(ligne)
        if actuelle["etat"] != entree["etat"] and _gagnante(actuelle, entree) is entree:
            ligne.etat = entree["etat"]
            ligne.decide_le = _date_lue(entree.get("decide_le"))
            ligne.note = entree.get("note") or ligne.note
            changees += 1
    if changees:
        db.session.commit()
    return changees


def _fiches_a_reappliquer():
    """(ligne, fiche) des effacements confirmés dont la fiche est revenue."""
    from app.models import EffacementRgpd, Participant
    from app.services.purge_rgpd import NOM_ANONYME
    lignes = EffacementRgpd.query.filter_by(etat="confirme").all()
    par_fiche: dict[int, list] = {}
    for ligne in lignes:
        par_fiche.setdefault(ligne.participant_id, []).append(ligne)
    if not par_fiche:
        return []
    resultat = []
    ids = list(par_fiche)
    for debut in range(0, len(ids), 500):
        for fiche in Participant.query.filter(Participant.id.in_(ids[debut:debut + 500]),
                                              Participant.nom != NOM_ANONYME).all():
            for ligne in par_fiche[fiche.id]:
                if _meme_creation(fiche.created_at, ligne.participant_cree_le, ligne.precision):
                    resultat.append((ligne, fiche))
                    break
    return resultat


def reappliquer() -> list[int]:
    """Après restauration : réanonymise les fiches revenues avec leur
    identité, pour les seuls effacements CONFIRMÉS. Idempotent. Renvoie les
    identifiants traités (commit inclus)."""
    from app.services.audit import enregistrer
    from app.services.purge_rgpd import anonymiser_participant
    try:
        exporter_en_attente()
    except RegistreErreur:
        journal.exception("Registre des effacements : recopie en attente avant réapplication")
    importer_ancien_registre()
    importer_fichier_en_base()
    traites = []
    for ligne, fiche in _fiches_a_reappliquer():
        anonymiser_participant(fiche)
        enregistrer("rgpd.reapplique_apres_restauration", cible=f"participant #{fiche.id}",
                    participant_id=fiche.id,
                    details={"motif": "supprimée" if ligne.nature == "suppression" else "anonymisée"})
        traites.append(fiche.id)
    db.session.commit()
    return traites


def synchroniser() -> dict:
    """Démarrage et maintenance : recopie en attente, ancien registre,
    réapplication. Ne lève jamais ; le rapport dit ce qui reste à faire."""
    rapport: dict = {"reappliques": [], "erreur": None}
    if chemin() is None:
        return rapport
    try:
        rapport["reappliques"] = reappliquer()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        rapport["erreur"] = getattr(exc, "detail", "") or exc.__class__.__name__
        journal.exception("Registre des effacements : synchronisation incomplète")
    return rapport


# ---------------------------------------------------------------------------
# Rapprochement des anciennes entrées « à vérifier »
# ---------------------------------------------------------------------------

def a_verifier() -> list:
    from app.models import EffacementRgpd
    return (EffacementRgpd.query.filter_by(etat="a_verifier")
            .order_by(EffacementRgpd.participant_id.asc(), EffacementRgpd.id.asc()).all())


def decider(entree_id: int, decision: str, *, user_id: int | None = None) -> str:
    """Tranche une entrée « à vérifier ». ``confirmer`` : l'effacement était
    voulu, il est appliqué ; ``ecarter`` : il avait été annulé, l'entrée ne
    sera jamais réappliquée. Sûr en cas de double envoi (mise à jour
    conditionnelle). Commit inclus. Renvoie un message."""
    from app.models import EffacementRgpd, Participant
    from app.services.audit import enregistrer
    from app.services.purge_rgpd import NOM_ANONYME, anonymiser_participant
    if decision not in {"confirmer", "ecarter"}:
        raise ValueError("décision inconnue")
    etat = "confirme" if decision == "confirmer" else "ecarte"
    table = EffacementRgpd.__table__
    maintenant = utcnow()
    resultat = db.session.execute(table.update().where(table.c.id == entree_id, table.c.etat == "a_verifier").values(
        etat=etat, decide_le=maintenant, decide_par_user_id=user_id, exporte_le=None))
    if resultat.rowcount != 1:
        db.session.rollback()
        return "Cette entrée a déjà été traitée."
    ligne = db.session.get(EffacementRgpd, entree_id)
    db.session.refresh(ligne)
    message = "Entrée écartée : l'effacement ne sera pas réappliqué."
    if etat == "confirme":
        fiche = db.session.get(Participant, ligne.participant_id)
        if fiche is not None and fiche.nom != NOM_ANONYME and _meme_creation(
                fiche.created_at, ligne.participant_cree_le, ligne.precision):
            anonymiser_participant(fiche)
            message = f"Effacement confirmé : la fiche n° {fiche.id} a été anonymisée."
        else:
            message = "Effacement confirmé (la fiche n'est plus présente sous son identité)."
    enregistrer("rgpd.registre_decision", cible=f"participant #{ligne.participant_id}",
                details={"decision": decision, "entree": ligne.cle})
    db.session.info["effacements_a_exporter"] = True
    db.session.commit()
    return message


def etat() -> dict:
    """Résumé pour Contrôle."""
    return {
        "tenu": chemin() is not None,
        "en_attente": nombre_en_attente(),
        "a_verifier": len(a_verifier()),
        "incident": incident(),
    }
