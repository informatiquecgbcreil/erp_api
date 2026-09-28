"""Rapprochement des règlements des anciens bulletins (défaut A).

La migration ``d2e4f6a8b013`` a classé chaque bulletin portant de l'argent
(voir ``reprise_reglements``). Ici, une personne habilitée tranche les cas
« à rapprocher » — et peut revenir sur une déduction « reporté » — :

- **Report confirmé** : la somme a bien été reportée (ou comptée ailleurs).
  Rien n'est ajouté. Si une somme « à qualifier » faisait double emploi avec
  le report, elle est annulée par contre-passation motivée (jamais effacée).
- **Encaissement distinct constaté** : la somme (ou la part non reportée) a
  été reçue et n'est nulle part. Elle est ajoutée comme encaissement « à
  qualifier », hors caisse : l'écran « À qualifier » existant fait préciser
  le ou les modes réels (espèces + chèque…) et s'il faut l'intégrer au
  théorique de caisse ou s'il a déjà été compté.

Garanties : une décision par ligne, quel que soit le nombre de clics ou de
personnes (mise à jour conditionnelle ; clé unique sur l'encaissement créé) ;
valeurs d'origine et preuves conservées ; auteur, date et note enregistrés ;
chaque décision est journalisée.

Comparaison avec une sauvegarde : depuis la PR #59, la colonne du bulletin
est recalculée (miroir du total versé) à l'ouverture de la liste. Sur une
base déjà passée par cette version, la valeur d'origine a pu être écrasée.
``comparer_avec_sauvegarde`` lit un lot de sauvegarde (sans le restaurer) et
classe, avec la même règle, les bulletins dont la valeur d'origine diffère.
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from app.extensions import db
from app.models import Encaissement, RapprochementBulletin
from app.services import reprise_reglements
from app.services.encaissements import DejaEnregistre, EncaissementErreur, contre_passer, verrouiller
from app.utils.dates import utcnow
from app.utils.montants import parse_montant

DECISIONS = {"report_confirme", "encaissement_constate"}


def a_rapprocher() -> list[RapprochementBulletin]:
    return (RapprochementBulletin.query
            .filter(RapprochementBulletin.classement == "a_rapprocher", RapprochementBulletin.decision.is_(None))
            .order_by(RapprochementBulletin.annee_scolaire.desc(), RapprochementBulletin.id.asc()).all())


def reportes_deduits() -> list[RapprochementBulletin]:
    return (RapprochementBulletin.query
            .filter(RapprochementBulletin.classement == "reporte", RapprochementBulletin.decision.is_(None))
            .order_by(RapprochementBulletin.annee_scolaire.desc(), RapprochementBulletin.id.asc()).all())


def decides() -> list[RapprochementBulletin]:
    return (RapprochementBulletin.query.filter(RapprochementBulletin.decision.isnot(None))
            .order_by(RapprochementBulletin.decide_le.desc()).limit(200).all())


def resume() -> dict:
    compte = {"a_rapprocher": 0, "reporte": 0, "suivi": 0, "decides": 0}
    for classement, decision, nombre in (db.session.query(RapprochementBulletin.classement,
                                                          RapprochementBulletin.decision,
                                                          db.func.count(RapprochementBulletin.id))
                                         .group_by(RapprochementBulletin.classement,
                                                   RapprochementBulletin.decision).all()):
        if decision is not None:
            compte["decides"] += nombre
        else:
            compte[classement] = compte.get(classement, 0) + nombre
    return compte


def _reclamer(ligne_id: int, decision: str, *, user_id, montant=None, note=None) -> RapprochementBulletin:
    """Pose la décision si (et seulement si) la ligne n'en a pas encore."""
    verrouiller("rapprochement", ligne_id)
    table = RapprochementBulletin.__table__
    resultat = db.session.execute(table.update().where(
        table.c.id == ligne_id, table.c.decision.is_(None), table.c.classement.in_(("a_rapprocher", "reporte"))
    ).values(decision=decision, decision_montant=montant, decision_note=(note or None),
             decide_par_user_id=user_id, decide_le=utcnow()))
    if resultat.rowcount != 1:
        raise DejaEnregistre("Ce bulletin a déjà été rapproché.")
    ligne = db.session.get(RapprochementBulletin, ligne_id)
    db.session.refresh(ligne)
    return ligne


def confirmer_report(ligne_id: int, *, user_id=None, note: str | None = None) -> RapprochementBulletin:
    """Rien n'est ajouté. Une somme « à qualifier » en double emploi est
    contre-passée (non commité)."""
    note = (note or "").strip()
    if len(note) < 3:
        raise EncaissementErreur("Indiquez sur quoi repose cette confirmation (elle est conservée au journal).")
    ligne = _reclamer(ligne_id, "report_confirme", user_id=user_id, note=note[:255])
    ancien = db.session.get(Encaissement, ligne.encaissement_ancien_id) if ligne.encaissement_ancien_id else None
    if ancien is not None and ancien.a_qualifier and not ancien.est_contre_passe:
        contre_passer(ancien, f"Rapprochement n° {ligne.id} : déjà reporté en versements ({note})"[:255],
                      user_id=user_id)
    return ligne


def constater_encaissement(ligne_id: int, montant, *, user_id=None, note: str | None = None) -> Encaissement:
    """Ajoute la somme non reportée comme encaissement « à qualifier », hors
    caisse (non commité). Jamais deux fois pour la même ligne."""
    ligne = db.session.get(RapprochementBulletin, ligne_id)
    if ligne is None:
        raise EncaissementErreur("Ligne de rapprochement introuvable.")
    valeur = parse_montant(montant)
    if valeur is None or valeur <= 0 or valeur > float(ligne.montant_origine) + 0.009:
        raise EncaissementErreur(
            f"Le montant constaté doit être supérieur à 0 € et au plus {ligne.montant_origine:.2f} € "
            "(la somme notée sur le bulletin).")
    valeur = round(valeur, 2)
    ligne = _reclamer(ligne_id, "encaissement_constate", user_id=user_id, montant=valeur, note=(note or "")[:255])
    encaissement = Encaissement(
        montant=valeur, mode="inconnu",
        date_encaissement=ligne.date_origine or date(ligne.annee_scolaire, 9, 1),
        inscription_annuelle_id=ligne.inscription_annuelle_id, participant_id=ligne.participant_id,
        foyer_id=ligne.foyer_id,
        libelle=f"Inscription annuelle {ligne.annee_scolaire}-{ligne.annee_scolaire + 1}",
        a_qualifier=True, hors_caisse=True,
        note_qualification=(f"Rapprochement n° {ligne.id} : somme notée sur le bulletin, non reportée ; "
                            f"dernier mode saisi : {ligne.mode_origine_label}.")[:255],
        source_ancienne=f"rapprochement:{ligne.id}",
        created_by_user_id=user_id,
    )
    db.session.add(encaissement)
    try:
        with db.session.begin_nested():
            db.session.flush()
    except Exception as exc:  # IntegrityError : une autre requête a gagné
        raise DejaEnregistre("Ce bulletin a déjà été rapproché.") from exc
    ligne.encaissement_id = encaissement.id
    return encaissement


# ---------------------------------------------------------------------------
# Comparaison avec une sauvegarde antérieure
# ---------------------------------------------------------------------------

_COLONNES = ("id", "reglement_montant", "reglement_mode", "reglement_date", "date_inscription")


def _decoder_copy(valeur: str):
    if valeur == "\\N":
        return None
    remplacements = {"\\t": "\t", "\\n": "\n", "\\r": "\r", "\\\\": "\\"}
    for code, texte in remplacements.items():
        valeur = valeur.replace(code, texte)
    return valeur


def _lire_dump_postgres(chemin: Path) -> list[dict]:
    """Lit la table inscription_annuelle d'un dump pg_dump en texte (COPY),
    sans rien exécuter."""
    lignes, colonnes, dedans = [], None, False
    with open(chemin, encoding="utf-8", errors="replace") as fichier:
        for brute in fichier:
            if not dedans:
                if brute.startswith("COPY ") and (" public.inscription_annuelle (" in brute
                                                  or " inscription_annuelle (" in brute):
                    colonnes = [c.strip().strip('"') for c in brute.split("(", 1)[1].split(")", 1)[0].split(",")]
                    dedans = True
                continue
            if brute.startswith("\\."):
                break
            valeurs = [_decoder_copy(v) for v in brute.rstrip("\n").split("\t")]
            lignes.append(dict(zip(colonnes, valeurs)))
    return lignes


def _lire_sqlite(chemin: Path) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="mcs-comparaison-") as dossier:
        copie = Path(dossier) / "copie.db"
        copie.write_bytes(chemin.read_bytes())
        connexion = sqlite3.connect(f"file:{copie}?mode=ro", uri=True)
        try:
            connexion.row_factory = sqlite3.Row
            colonnes = {r[1] for r in connexion.execute("PRAGMA table_info(inscription_annuelle)")}
            if "reglement_montant" not in colonnes:
                return []
            return [dict(r) for r in connexion.execute(
                "SELECT " + ", ".join(c for c in _COLONNES if c in colonnes) + " FROM inscription_annuelle")]
        finally:
            connexion.close()


def valeurs_de_la_sauvegarde(base: str) -> dict[int, dict]:
    """Règlements notés sur les bulletins dans un lot (lecture seule)."""
    from app.services.sauvegarde import dossier_sauvegardes, lister_lots, verifier_lot
    dossier = dossier_sauvegardes()
    if base not in {lot["base"] for lot in lister_lots()}:
        raise EncaissementErreur("Sauvegarde introuvable : choisissez-en une dans la liste.")
    if not verifier_lot(base)["ok"]:
        raise EncaissementErreur("Cette sauvegarde n'est pas lisible en entier : choisissez-en une autre.")
    if (dossier / f"{base}.sql").exists():
        lignes = _lire_dump_postgres(dossier / f"{base}.sql")
    elif (dossier / f"{base}.db").exists():
        lignes = _lire_sqlite(dossier / f"{base}.db")
    else:
        raise EncaissementErreur("Sauvegarde introuvable.")
    valeurs = {}
    for ligne in lignes:
        try:
            montant = float(ligne.get("reglement_montant") or 0)
            bid = int(ligne["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if montant > 0 and reprise_reglements._fini(montant):
            valeurs[bid] = {"montant": round(montant, 2), "mode": ligne.get("reglement_mode") or None,
                            "date": ligne.get("reglement_date") or ligne.get("date_inscription")}
    return valeurs


def comparer_avec_sauvegarde(base: str) -> dict:
    """Classe les bulletins dont la valeur notée dans le lot diffère de celle
    déjà classée. Rejouable sans doublon. Commit inclus."""
    valeurs = valeurs_de_la_sauvegarde(base)
    connues = {}
    for ligne in RapprochementBulletin.query.all():
        connues.setdefault(ligne.inscription_annuelle_id, set()).add(round(float(ligne.montant_origine), 2))
    differentes = {bid: v for bid, v in valeurs.items() if v["montant"] not in connues.get(bid, set())}
    compte = reprise_reglements.classer_tout(db.session.connection(), source="sauvegarde",
                                             source_detail=base[:120], valeurs=differentes)
    db.session.commit()
    compte["lues"] = len(valeurs)
    return compte
