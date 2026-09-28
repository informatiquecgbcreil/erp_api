"""Classement des règlements notés sur les anciens bulletins (défaut A).

Contexte. Avant la PR #59, l'argent reçu à l'accueil avant la création de
la fiche était noté sur le bulletin (``reglement_montant``, dernier mode,
dernière date). À la création des cotisations, il était reporté en
versements (``paiement``) avec pour commentaire « Inscription annuelle
AAAA-AAAA », sauf si un versement existait déjà sur l'une des cotisations de
l'année. Ensuite, la colonne devenait un simple miroir du total versé.

La reprise de la PR #59 (``b5d8e3a1f264``) ignorait un bulletin dès qu'un
versement quelconque existait pour la personne ou son foyer la même année.
Or ce versement ne prouve pas le report : 30 € en espèces sur le bulletin et
un chèque de 10 € saisi sur la fiche donnaient 10 € suivis et 30 € disparus
du suivi, sans alerte.

Ce module classe chaque bulletin, sans jamais rien ajouter en caisse :

- ``reporte`` : le report est démontré — versements « Inscription annuelle
  AAAA-AAAA » des cotisations du bulletin couvrant la somme — ou le montant
  est exactement le total versé sur ces cotisations (miroir de l'ancienne
  version) ; la preuve retenue est enregistrée et reste révisable ;
- ``suivi`` : la somme est déjà dans le nouveau registre (encaissement du
  bulletin, y compris « à qualifier »), ou une personne l'a déjà traitée ;
- ``a_rapprocher`` : ni prouvé ni exclu (report partiel, autres versements,
  plusieurs bulletins la même année pour les mêmes personnes, double emploi
  possible avec une somme « à qualifier ») : une personne tranche dans
  Caisse → Rapprochement des bulletins. Rien n'est ajouté automatiquement :
  ajouter 30 € d'office créerait un doublon si la somme avait été reportée.

Un seul code pour les deux parcours : la migration corrective
``d2e4f6a8b013`` appelle ``classer_tout`` sur une base reprise d'une
installation ancienne comme sur une base déjà passée par la PR #59, et la
comparaison avec une sauvegarde (valeur d'origine écrasée par le miroir
depuis la PR #59) appelle ``classer`` avec la valeur lue dans le lot.
``VERSION`` est notée sur chaque ligne ; un changement de règle exige une
nouvelle migration.

Ce module n'importe ni les modèles ni l'application : il ne travaille que
sur une connexion SQLAlchemy, pour rester exécutable pendant une migration.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime

import sqlalchemy as sa

VERSION = 1
TOLERANCE = 0.009


def _fini(valeur) -> bool:
    try:
        return valeur is not None and math.isfinite(float(valeur))
    except (TypeError, ValueError):
        return False


def _jour(valeur):
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str) and valeur:
        try:
            return date.fromisoformat(valeur[:10])
        except ValueError:
            return None
    return None


def _cents(valeur) -> float:
    return round(float(valeur or 0), 2)


def personnes_couvertes(bind, bulletin_id: int, participant_id) -> list[int]:
    ids = [int(participant_id)] if participant_id else []
    for (pid,) in bind.execute(sa.text(
            "SELECT participant_id FROM inscription_annuelle_membre WHERE inscription_id = :b "
            "AND participant_id IS NOT NULL ORDER BY id"), {"b": bulletin_id}):
        if int(pid) not in ids:
            ids.append(int(pid))
    return ids


def versements_candidats(bind, annee: int, personnes: list[int], foyer_id) -> list[dict]:
    """Versements des cotisations de l'année que ce bulletin pouvait régler
    (mêmes règles que l'ancienne version : personnes couvertes ou foyer)."""
    conditions, params = [], {"a": annee}
    if personnes:
        conditions.append("c.participant_id IN (" + ", ".join(f":p{i}" for i in range(len(personnes))) + ")")
        params.update({f"p{i}": pid for i, pid in enumerate(personnes)})
    if foyer_id:
        conditions.append("c.foyer_id = :f")
        params["f"] = foyer_id
    if not conditions:
        return []
    lignes = bind.execute(sa.text(
        "SELECT p.id, p.montant, p.mode, p.date_paiement, p.commentaire, p.cotisation_id, p.encaissement_id "
        "FROM paiement p JOIN cotisation c ON c.id = p.cotisation_id "
        "WHERE c.annee_scolaire = :a AND (" + " OR ".join(conditions) + ") ORDER BY p.id"), params).fetchall()
    return [{"id": l[0], "montant": _cents(l[1]) if _fini(l[1]) else None, "mode": l[2],
             "date": _jour(l[3]).isoformat() if _jour(l[3]) else None, "commentaire": l[4],
             "cotisation_id": l[5], "encaissement_id": l[6]} for l in lignes]


def encaissements_du_bulletin(bind, bulletin_id: int) -> list[dict]:
    lignes = bind.execute(sa.text(
        "SELECT e.id, e.montant, e.source_ancienne, e.a_qualifier, e.origine_id, "
        "(SELECT count(*) FROM encaissement x WHERE x.origine_id = e.id) "
        "FROM encaissement e WHERE e.inscription_annuelle_id = :b ORDER BY e.id"), {"b": bulletin_id}).fetchall()
    return [{"id": l[0], "montant": l[1], "source": l[2], "a_qualifier": bool(l[3]), "origine_id": l[4],
             "contre_passe": bool(l[5])} for l in lignes]


def classer(bind, bulletin: dict, *, autres_bulletins: list[dict] | None = None) -> dict:
    """Classement d'un bulletin. ``bulletin`` : id, annee_scolaire,
    participant_id, foyer_id, montant (valeur d'origine), mode, date.
    ``autres_bulletins`` : bulletins de la même année portant de l'argent,
    pour détecter un partage des mêmes versements. Ne modifie rien."""
    bid, annee = int(bulletin["id"]), int(bulletin["annee_scolaire"])
    montant = _cents(bulletin["montant"])
    libelle = f"Inscription annuelle {annee}-{annee + 1}"
    personnes = personnes_couvertes(bind, bid, bulletin.get("participant_id"))
    versements = versements_candidats(bind, annee, personnes, bulletin.get("foyer_id"))
    signes = [v for v in versements if v["montant"] is not None and (v["commentaire"] or "").strip() == libelle]
    prouve = _cents(sum(v["montant"] for v in signes))
    total = _cents(sum(v["montant"] for v in versements if v["montant"] is not None))
    encaissements = encaissements_du_bulletin(bind, bid)
    a_qualifier_ancien = next((e for e in encaissements if e["source"] == f"bulletin:{bid}"), None)
    propres = [e for e in encaissements if e["origine_id"] is None and e["source"] != f"bulletin:{bid}"]

    partage = []
    for autre in autres_bulletins or []:
        if int(autre["id"]) == bid:
            continue
        ses_personnes = personnes_couvertes(bind, int(autre["id"]), autre.get("participant_id"))
        if set(ses_personnes) & set(personnes) or (autre.get("foyer_id") and autre.get("foyer_id") == bulletin.get("foyer_id")):
            partage.append(int(autre["id"]))

    preuve = {"versements": versements, "versements_du_report": [v["id"] for v in signes],
              "encaissements_du_bulletin": [e["id"] for e in encaissements], "bulletins_partageant": partage,
              "personnes": personnes}
    resultat = {"classement": "a_rapprocher", "montant_prouve": prouve, "montant_ecart": montant,
                "preuve": preuve, "motif": "", "encaissement_ancien_id": None}

    if a_qualifier_ancien is not None:
        resultat["encaissement_ancien_id"] = a_qualifier_ancien["id"]
        traite = not a_qualifier_ancien["a_qualifier"] or a_qualifier_ancien["contre_passe"]
        if traite:
            resultat.update(classement="suivi", montant_ecart=0.0,
                            motif="Somme reprise « à qualifier » et déjà traitée par une personne.")
        elif prouve > TOLERANCE:
            resultat.update(motif=(f"Somme inscrite « à qualifier » alors que {prouve:.2f} € ont déjà été reportés "
                                   f"en versements « {libelle} » : double emploi probable."),
                            montant_ecart=_cents(max(0.0, montant - prouve)))
        else:
            resultat.update(classement="suivi", montant_ecart=0.0,
                            motif="Somme reprise dans « À qualifier » (aucun report en versements).")
        return resultat

    if propres:
        resultat.update(classement="suivi", montant_ecart=0.0,
                        motif="Règlement enregistré en encaissements sur le bulletin (nouveau registre).")
        return resultat

    if partage:
        resultat.update(motif=(f"Plusieurs bulletins {annee}-{annee + 1} portent de l'argent pour les mêmes "
                               f"personnes (n° {', '.join(map(str, partage))}) : les versements ne peuvent pas "
                               "être attribués automatiquement."))
        return resultat

    if not versements:
        resultat.update(motif="Aucun versement ni encaissement en face de cette somme : jamais reprise.")
        return resultat

    if prouve >= montant - TOLERANCE:
        resultat.update(classement="reporte", montant_ecart=0.0,
                        motif=f"Reporté : versements « {libelle} » de {prouve:.2f} € sur les cotisations du bulletin.")
    elif abs(total - montant) <= TOLERANCE and prouve <= TOLERANCE:
        resultat.update(classement="reporte", montant_ecart=0.0,
                        motif=(f"Montant égal au total versé sur les cotisations du bulletin ({total:.2f} €) : "
                               "valeur recopiée par l'ancienne version. Déduction révisable."))
    elif prouve > TOLERANCE:
        resultat.update(montant_ecart=_cents(montant - prouve),
                        motif=(f"Report partiel : {prouve:.2f} € reportés en versements « {libelle} », "
                               f"{montant - prouve:.2f} € sans trace."))
    else:
        resultat.update(motif=(f"Des versements existent ({total:.2f} €) mais aucun ne provient du bulletin : "
                               "rien ne prouve que la somme notée a été reportée."))
    return resultat


def _bulletins_avec_argent(bind) -> list[dict]:
    lignes = bind.execute(sa.text(
        "SELECT id, annee_scolaire, participant_id, foyer_id, reglement_montant, reglement_mode, reglement_date, "
        "date_inscription, statut FROM inscription_annuelle WHERE reglement_montant > 0 ORDER BY id")).fetchall()
    resultat = []
    for l in lignes:
        if not _fini(l[4]):
            continue  # Montant non fini : Contrôle -> Anomalies de montants.
        resultat.append({"id": l[0], "annee_scolaire": l[1], "participant_id": l[2], "foyer_id": l[3],
                         "montant": _cents(l[4]), "mode": l[5], "date": _jour(l[6]) or _jour(l[7]),
                         "statut": l[8]})
    return resultat


def inserer(bind, bulletin: dict, classement: dict, *, cle: str, source: str, source_detail: str | None = None,
            maintenant: datetime | None = None) -> bool:
    """Insère la ligne de rapprochement si la clé est nouvelle. Idempotent."""
    if bind.execute(sa.text("SELECT 1 FROM rapprochement_bulletin WHERE cle = :c"), {"c": cle}).first():
        return False
    bind.execute(sa.text(
        "INSERT INTO rapprochement_bulletin (cle, inscription_annuelle_id, annee_scolaire, participant_id, foyer_id, "
        "montant_origine, mode_origine, date_origine, statut_bulletin, source, source_detail, classement, "
        "montant_prouve, montant_ecart, preuve, motif, encaissement_ancien_id, version_classement, cree_le) "
        "VALUES (:cle, :b, :a, :p, :f, :m, :mode, :d, :statut, :source, :detail, :classement, :prouve, :ecart, "
        ":preuve, :motif, :ancien, :version, :cree)"), {
            "cle": cle, "b": bulletin["id"], "a": bulletin["annee_scolaire"], "p": bulletin.get("participant_id"),
            "f": bulletin.get("foyer_id"), "m": bulletin["montant"], "mode": (bulletin.get("mode") or None),
            "d": bulletin.get("date"), "statut": bulletin.get("statut"), "source": source, "detail": source_detail,
            "classement": classement["classement"], "prouve": classement["montant_prouve"],
            "ecart": classement["montant_ecart"], "preuve": json.dumps(classement["preuve"], ensure_ascii=False),
            "motif": classement["motif"][:255], "ancien": classement["encaissement_ancien_id"], "version": VERSION,
            "cree": maintenant or datetime.utcnow()})
    return True


def classer_tout(bind, *, source: str = "base", source_detail: str | None = None,
                 valeurs: dict[int, dict] | None = None, maintenant: datetime | None = None) -> dict:
    """Classe tous les bulletins portant de l'argent. ``valeurs`` : valeurs
    d'origine lues ailleurs (sauvegarde), par numéro de bulletin ; sinon la
    colonne actuelle. Rejouable : une clé déjà classée n'est pas reprise."""
    actuels = _bulletins_avec_argent(bind)
    if valeurs is not None:
        par_id = {b["id"]: b for b in actuels}
        bulletins = []
        for bid, origine in valeurs.items():
            base = dict(par_id.get(bid) or {})
            if not base:
                ligne = bind.execute(sa.text(
                    "SELECT id, annee_scolaire, participant_id, foyer_id, statut FROM inscription_annuelle "
                    "WHERE id = :b"), {"b": bid}).first()
                if ligne is None:
                    continue  # Bulletin supprimé depuis : rien à rattacher.
                base = {"id": ligne[0], "annee_scolaire": ligne[1], "participant_id": ligne[2],
                        "foyer_id": ligne[3], "statut": ligne[4]}
            base.update(montant=_cents(origine["montant"]), mode=origine.get("mode"),
                        date=_jour(origine.get("date")) or base.get("date"))
            bulletins.append(base)
    else:
        bulletins = actuels
    par_annee: dict[int, list[dict]] = {}
    for b in bulletins:
        par_annee.setdefault(int(b["annee_scolaire"]), []).append(b)
    compte = {"reporte": 0, "suivi": 0, "a_rapprocher": 0, "deja_classes": 0}
    for b in bulletins:
        cle = f"bulletin:{b['id']}" if source == "base" else f"bulletin:{b['id']}:{source}:{int(round(b['montant'] * 100))}"
        classement = classer(bind, b, autres_bulletins=par_annee[int(b["annee_scolaire"])])
        if inserer(bind, b, classement, cle=cle, source=source, source_detail=source_detail, maintenant=maintenant):
            compte[classement["classement"]] += 1
        else:
            compte["deja_classes"] += 1
    return compte
