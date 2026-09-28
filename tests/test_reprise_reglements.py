"""Règlements des anciens bulletins (défaut A de la consolidation après la PR #59).

Reproduction du défaut : un bulletin rattaché à une personne indique 30 € en
espèces ; une cotisation de cette personne porte un autre versement de 10 €
par chèque. La reprise ``b5d8e3a1f264`` ne gardait que les 10 € ; les 30 €
restaient dans la colonne du bulletin, absents du suivi et de « À
qualifier ». Désormais la migration ``d2e4f6a8b013`` classe chaque bulletin
(reporté prouvé / déjà suivi / à rapprocher) et une personne tranche les cas
ambigus, sans doublon possible.

Les données sont écrites comme les laissait l'ancienne version (versements
créés hors du circuit d'encaissement, colonne du bulletin renseignée à la
main), puis la reprise de la PR #59 est rejouée telle quelle.
"""
from __future__ import annotations

import importlib.util
import threading
import uuid
from datetime import date
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
ANNEE = 2031


def _migration(nom):
    chemin = next((RACINE / "migrations/versions").glob(f"{nom}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{nom}", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _personne(db, **extra):
    from app.models import Participant
    p = Participant(nom=f"Regl{uuid.uuid4().hex[:8]}", prenom="Test", created_secteur="Familles", **extra)
    db.session.add(p)
    db.session.flush()
    return p


def _cotisation(db, *, participant=None, foyer_id=None, type_cotisation="participation", du=60.0, annee=ANNEE):
    from app.models import Cotisation
    c = Cotisation(annee_scolaire=annee, type_cotisation=type_cotisation, montant_du=du,
                   participant_id=getattr(participant, "id", None), foyer_id=foyer_id,
                   date_reference=date(annee, 9, 1))
    db.session.add(c)
    db.session.flush()
    return c


def _versement_ancien(db, cotisation, montant, mode, commentaire=None, jour=None):
    """Versement tel que l'ancienne version l'écrivait (sans encaissement) ;
    la reprise b5d8 lui donnera son encaissement identique."""
    from sqlalchemy import text
    db.session.execute(text(
        "INSERT INTO paiement (cotisation_id, montant, date_paiement, mode, commentaire, created_at) "
        "VALUES (:c, :m, :d, :mode, :com, :t)"), {"c": cotisation.id, "m": montant, "mode": mode,
                                                   "d": jour or date(cotisation.annee_scolaire, 9, 15),
                                                   "com": commentaire, "t": date(cotisation.annee_scolaire, 9, 15)})


def _bulletin(db, *, participant=None, foyer_id=None, montant=30.0, mode="especes", annee=ANNEE, membres=()):
    from sqlalchemy import text
    from app.models import InscriptionAnnuelle, InscriptionAnnuelleMembre
    b = InscriptionAnnuelle(annee_scolaire=annee, nom=f"Bul{uuid.uuid4().hex[:6]}", prenom="Test",
                            participant_id=getattr(participant, "id", None), foyer_id=foyer_id,
                            date_inscription=date(annee, 9, 10))
    db.session.add(b)
    db.session.flush()
    for m in membres:
        db.session.add(InscriptionAnnuelleMembre(inscription_id=b.id, prenom="Membre", participant_id=m.id))
    db.session.flush()
    db.session.execute(text("UPDATE inscription_annuelle SET reglement_montant = :m, reglement_mode = :mode, "
                            "reglement_date = :d WHERE id = :b"),
                       {"m": montant, "mode": mode, "d": date(annee, 9, 12), "b": b.id})
    return b


def _reprise_pr59_puis_correctif(db):
    """Même enchaînement qu'une mise à jour : b5d8 (PR #59, inchangée) puis
    le classement de la migration corrective."""
    from app.services.reprise_reglements import classer_tout
    _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())
    compte = classer_tout(db.session.connection())
    db.session.commit()
    return compte


def _ligne(bid):
    from app.models import RapprochementBulletin
    return RapprochementBulletin.query.filter_by(cle=f"bulletin:{bid}").one()


def _encaissements_du_bulletin(bid):
    from app.models import Encaissement
    return Encaissement.query.filter_by(inscription_annuelle_id=bid).order_by(Encaissement.id).all()


def _caisse(app):
    from app.services.caisse import etat_caisse
    with app.app_context():
        e = etat_caisse()
        return e["encaissements_especes"], e["cheques_en_attente"]


# ---------------------------------------------------------------------------
# Scénario de l'audit : 30 € espèces sur le bulletin + 10 € chèque distinct
# ---------------------------------------------------------------------------

def test_a_30_especes_plus_cheque_distinct_ne_disparait_plus(app, admin_client):
    from app.extensions import db
    from app.models import Encaissement
    with app.app_context():
        p = _personne(db)
        c = _cotisation(db, participant=p)
        _versement_ancien(db, c, 10.0, "cheque", "chèque saisi sur la fiche")
        b = _bulletin(db, participant=p, montant=30.0, mode="especes")
        db.session.commit()
        bid = b.id
        _reprise_pr59_puis_correctif(db)
        # La reprise de la PR #59 seule ne gardait que les 10 € :
        assert _encaissements_du_bulletin(bid) == []
        ligne = _ligne(bid)
        assert (ligne.classement, ligne.montant_origine, ligne.mode_origine, ligne.montant_ecart) == \
            ("a_rapprocher", 30.0, "especes", 30.0)
        [versement] = ligne.preuve_detail["versements"]
        assert (versement["montant"], versement["mode"]) == (10.0, "cheque")
        assert ligne.preuve_detail["versements_du_report"] == []
        lid = ligne.id
    caisse_avant = _caisse(app)
    page = admin_client.get("/caisse/rapprochement-bulletins").get_data(as_text=True)
    assert "30.00 €" in page and "10.00 €" in page
    assert "à rapprocher" in admin_client.get("/caisse").get_data(as_text=True)

    # Somme distincte constatée : elle rejoint « À qualifier », hors caisse.
    r = admin_client.post("/caisse/rapprochement-bulletins", data={
        "action": "constater", "ligne_id": lid, "montant": "30", "note": "cahier d'accueil du 12/09"})
    assert r.status_code == 302
    # Double clic : refusé, aucune seconde somme.
    r = admin_client.post("/caisse/rapprochement-bulletins", data={
        "action": "constater", "ligne_id": lid, "montant": "30", "note": "double clic"}, follow_redirects=True)
    assert "déjà été rapproché" in r.get_data(as_text=True)
    with app.app_context():
        [constate] = Encaissement.query.filter_by(source_ancienne=f"rapprochement:{lid}").all()
        assert (constate.montant, constate.mode, constate.a_qualifier, constate.hors_caisse) == (30.0, "inconnu", True, True)
        assert constate.inscription_annuelle_id == bid
        ligne = _ligne(bid)
        assert ligne.decision == "encaissement_constate" and ligne.encaissement_id == constate.id
        assert ligne.decide_par_user_id is not None and ligne.decision_note == "cahier d'accueil du 12/09"
        eid = constate.id
    assert _caisse(app) == caisse_avant                  # rien en caisse tant que non qualifié
    # Qualification : 20 € espèces + 10 € chèque, jamais comptés.
    admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes", "montant_0": "20",
                                                   "mode_1": "cheque", "montant_1": "10", "caisse": "dans"})
    # Renvoi du formulaire : refusé, rien de plus en caisse.
    admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes", "montant_0": "30",
                                                   "caisse": "dans"})
    especes, cheques = _caisse(app)
    assert (round(especes - caisse_avant[0], 2), round(cheques - caisse_avant[1], 2)) == (20.0, 10.0)


def test_a_report_integral_demontre_sans_doublon(app):
    from app.extensions import db
    from app.models import Encaissement
    with app.app_context():
        p = _personne(db)
        adh = _cotisation(db, participant=p, type_cotisation="adhesion_individuelle", du=10.0)
        part = _cotisation(db, participant=p, du=60.0)
        libelle = f"Inscription annuelle {ANNEE}-{ANNEE + 1}"
        _versement_ancien(db, adh, 10.0, "especes", libelle)
        _versement_ancien(db, part, 20.0, "especes", libelle)
        b = _bulletin(db, participant=p, montant=30.0, mode="especes")
        db.session.commit()
        avant = Encaissement.query.count()
        _reprise_pr59_puis_correctif(db)
        ligne = _ligne(b.id)
        assert (ligne.classement, ligne.montant_prouve, ligne.montant_ecart) == ("reporte", 30.0, 0.0)
        assert len(ligne.preuve_detail["versements_du_report"]) == 2
        # Seuls les deux versements reçoivent leur encaissement (b5d8) : rien pour le bulletin.
        assert Encaissement.query.count() == avant + 2
        assert _encaissements_du_bulletin(b.id) == []


def test_a_report_partiel(app, admin_client):
    from app.extensions import db
    from app.models import Encaissement
    with app.app_context():
        p = _personne(db)
        c = _cotisation(db, participant=p)
        _versement_ancien(db, c, 20.0, "especes", f"Inscription annuelle {ANNEE}-{ANNEE + 1}")
        b = _bulletin(db, participant=p, montant=30.0)
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        ligne = _ligne(b.id)
        assert (ligne.classement, ligne.montant_prouve, ligne.montant_ecart) == ("a_rapprocher", 20.0, 10.0)
        lid = ligne.id
    # Plus que la somme notée : refusé.
    r = admin_client.post("/caisse/rapprochement-bulletins", data={
        "action": "constater", "ligne_id": lid, "montant": "31", "note": "x"}, follow_redirects=True)
    assert "au plus 30.00" in r.get_data(as_text=True)
    admin_client.post("/caisse/rapprochement-bulletins", data={
        "action": "constater", "ligne_id": lid, "montant": "10", "note": "reste reçu en espèces"})
    with app.app_context():
        [e] = Encaissement.query.filter_by(source_ancienne=f"rapprochement:{lid}").all()
        assert e.montant == 10.0 and e.a_qualifier


def test_a_plusieurs_versements_et_plusieurs_modes(app):
    from app.extensions import db
    with app.app_context():
        p = _personne(db)
        adh = _cotisation(db, participant=p, type_cotisation="adhesion_individuelle", du=10.0)
        part = _cotisation(db, participant=p, du=60.0)
        libelle = f"Inscription annuelle {ANNEE}-{ANNEE + 1}"
        _versement_ancien(db, adh, 10.0, "especes", libelle)
        _versement_ancien(db, part, 5.0, "especes", libelle)
        _versement_ancien(db, part, 15.0, "cheque", libelle)
        _versement_ancien(db, part, 12.0, "carte", "réglé sur la fiche")
        b = _bulletin(db, participant=p, montant=30.0, mode="cheque")   # dernier mode seulement
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        ligne = _ligne(b.id)
        assert (ligne.classement, ligne.montant_prouve) == ("reporte", 30.0)
        assert {v["mode"] for v in ligne.preuve_detail["versements"]} == {"especes", "cheque", "carte"}


def test_a_versement_d_un_autre_membre_du_foyer(app):
    """Un versement sur l'adhésion familiale (payée par un autre membre) ne
    prouve pas le report de ce qui est noté sur le bulletin."""
    from app.extensions import db
    from app.models import Foyer
    with app.app_context():
        foyer = Foyer(nom=f"Foyer {uuid.uuid4().hex[:6]}")
        db.session.add(foyer)
        db.session.flush()
        p, conjoint = _personne(db, foyer_id=foyer.id), _personne(db, foyer_id=foyer.id)
        familiale = _cotisation(db, foyer_id=foyer.id, type_cotisation="adhesion_familiale", du=15.0)
        _versement_ancien(db, familiale, 15.0, "carte", f"payé par {conjoint.id}")
        b = _bulletin(db, participant=p, foyer_id=foyer.id, montant=30.0)
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        assert _encaissements_du_bulletin(b.id) == []        # la PR #59 l'ignorait
        ligne = _ligne(b.id)
        assert (ligne.classement, ligne.montant_ecart) == ("a_rapprocher", 30.0)


def test_a_plusieurs_bulletins_pour_la_meme_personne(app):
    from app.extensions import db
    with app.app_context():
        p = _personne(db)
        c = _cotisation(db, participant=p)
        libelle = f"Inscription annuelle {ANNEE}-{ANNEE + 1}"
        _versement_ancien(db, c, 30.0, "especes", libelle)
        b1 = _bulletin(db, participant=p, montant=30.0)
        b2 = _bulletin(db, participant=p, montant=20.0)
        # Une autre année, indépendante : prouvée.
        c_prec = _cotisation(db, participant=p, annee=ANNEE - 1)
        _versement_ancien(db, c_prec, 25.0, "cheque", f"Inscription annuelle {ANNEE - 1}-{ANNEE}")
        b3 = _bulletin(db, participant=p, montant=25.0, annee=ANNEE - 1)
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        for bid, autre in ((b1.id, b2.id), (b2.id, b1.id)):
            ligne = _ligne(bid)
            assert ligne.classement == "a_rapprocher" and ligne.preuve_detail["bulletins_partageant"] == [autre]
        assert _ligne(b3.id).classement == "reporte"


def test_a_bulletin_sans_fiche(app):
    from app.extensions import db
    with app.app_context():
        b = _bulletin(db, montant=25.0, mode="cheque")
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        [ancien] = _encaissements_du_bulletin(b.id)
        assert ancien.a_qualifier and ancien.montant == 25.0 and ancien.source_ancienne == f"bulletin:{b.id}"
        ligne = _ligne(b.id)
        assert ligne.classement == "suivi" and ligne.encaissement_ancien_id == ancien.id


def test_a_reglement_deja_dans_le_nouveau_registre(app):
    from app.extensions import db
    from app.services.encaissements import enregistrer
    with app.app_context():
        p = _personne(db)
        b = _bulletin(db, participant=p, montant=40.0)
        enregistrer(40.0, "especes", date_encaissement=date(ANNEE, 9, 12), inscription=b)
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        assert _ligne(b.id).classement == "suivi"
        assert len(_encaissements_du_bulletin(b.id)) == 1


def test_a_anomalie_deja_traitee_et_double_emploi(app, admin_client):
    """(1) somme « à qualifier » déjà tranchée par une personne : suivie, on
    n'y touche pas ; (2) somme « à qualifier » alors que le report existe :
    à rapprocher ; confirmer le report annule la ligne en double (contre-
    passation motivée), sans rien changer en caisse."""
    from app.extensions import db
    from app.models import Encaissement
    from app.services.encaissements import a_qualifier, qualifier
    with app.app_context():
        traite = _bulletin(db, montant=18.0)
        db.session.commit()
        _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())
        db.session.commit()
        [ancien] = _encaissements_du_bulletin(traite.id)
        qualifier(ancien, [("especes", 18.0)], dans_caisse=False)
        db.session.commit()
        # Bulletin sans titulaire mais dont un membre a une fiche : la PR #59
        # ne regardait que le titulaire et a inscrit la somme « à qualifier »
        # alors que le report existait sur la cotisation du membre.
        membre = _personne(db)
        c = _cotisation(db, participant=membre)
        _versement_ancien(db, c, 22.0, "especes", f"Inscription annuelle {ANNEE}-{ANNEE + 1}")
        double = _bulletin(db, montant=22.0, membres=[membre])
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        assert _ligne(traite.id).classement == "suivi"
        ligne = _ligne(double.id)
        assert ligne.classement == "a_rapprocher" and "double emploi" in ligne.motif
        lid, eid = ligne.id, ligne.encaissement_ancien_id
    caisse_avant = _caisse(app)
    r = admin_client.post("/caisse/rapprochement-bulletins", data={"action": "confirmer", "ligne_id": lid, "note": ""},
                          follow_redirects=True)
    assert "Indiquez sur quoi repose" in r.get_data(as_text=True)
    admin_client.post("/caisse/rapprochement-bulletins", data={
        "action": "confirmer", "ligne_id": lid, "note": "report visible sur la fiche du membre"})
    with app.app_context():
        ancien = db.session.get(Encaissement, eid)
        assert ancien.est_contre_passe
        assert eid not in [e.id for e in a_qualifier()]
        assert _ligne(double.id).decision == "report_confirme"
    assert _caisse(app) == caisse_avant


def test_a_reparation_rejouee_sans_effet(app):
    from app.extensions import db
    from app.models import Encaissement, RapprochementBulletin
    with app.app_context():
        p = _personne(db)
        c = _cotisation(db, participant=p)
        _versement_ancien(db, c, 10.0, "cheque")
        b = _bulletin(db, participant=p, montant=30.0)
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        lignes, encaissements = RapprochementBulletin.query.count(), Encaissement.query.count()
        for _ in range(3):
            compte = _reprise_pr59_puis_correctif(db)
            assert compte["a_rapprocher"] == compte["reporte"] == compte["suivi"] == 0
        assert (RapprochementBulletin.query.count(), Encaissement.query.count()) == (lignes, encaissements)
        assert _ligne(b.id).classement == "a_rapprocher"


def test_a_decisions_simultanees(app, dialecte):
    """Deux personnes tranchent la même ligne au même moment : une seule
    décision, un seul encaissement. Sur PostgreSQL le verrou et la mise à
    jour conditionnelle départagent ; sur SQLite, la seconde écriture est
    refusée par le moteur."""
    from sqlalchemy.exc import OperationalError
    from app.extensions import db
    from app.models import Encaissement
    from app.services import rapprochement_reglements as rr
    from app.services.encaissements import DejaEnregistre
    with app.app_context():
        p = _personne(db)
        b = _bulletin(db, participant=p, montant=30.0)
        c = _cotisation(db, participant=p)
        _versement_ancien(db, c, 10.0, "cheque")
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        lid = _ligne(b.id).id
    depart = threading.Barrier(2)
    resultats = []

    def decider(action):
        with app.app_context():
            try:
                depart.wait(10)
                if action == "constater":
                    rr.constater_encaissement(lid, "30", note="personne A")
                else:
                    rr.confirmer_report(lid, note="personne B")
                db.session.commit()
                resultats.append("ok")
            except (DejaEnregistre, OperationalError) as exc:
                db.session.rollback()
                resultats.append(type(exc).__name__)
            finally:
                db.session.remove()

    fils = [threading.Thread(target=decider, args=(a,)) for a in ("constater", "confirmer")]
    for f in fils:
        f.start()
    for f in fils:
        f.join(60)
    assert resultats.count("ok") == 1, resultats
    with app.app_context():
        ligne = _ligne(b.id)
        crees = Encaissement.query.filter_by(source_ancienne=f"rapprochement:{lid}").count()
        assert (ligne.decision == "encaissement_constate") == (crees == 1)
        assert crees <= 1


def test_a_qualification_simultanee(app):
    from sqlalchemy.exc import OperationalError
    from app.extensions import db
    from app.models import Encaissement
    from app.services.encaissements import DejaEnregistre, qualifier
    with app.app_context():
        b = _bulletin(db, montant=40.0)
        db.session.commit()
        _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())
        db.session.commit()
        [ancien] = _encaissements_du_bulletin(b.id)
        eid = ancien.id
    depart = threading.Barrier(2)
    resultats = []

    def qualifier_en_parallele(parts):
        with app.app_context():
            try:
                e = db.session.get(Encaissement, eid)
                depart.wait(10)
                qualifier(e, parts, dans_caisse=True)
                db.session.commit()
                resultats.append("ok")
            except (DejaEnregistre, OperationalError) as exc:
                db.session.rollback()
                resultats.append(type(exc).__name__)
            finally:
                db.session.remove()

    fils = [threading.Thread(target=qualifier_en_parallele, args=(parts,))
            for parts in ([("especes", 40.0)], [("especes", 20.0), ("cheque", 20.0)])]
    for f in fils:
        f.start()
    for f in fils:
        f.join(60)
    assert resultats.count("ok") == 1, resultats
    with app.app_context():
        total = sum(e.montant for e in _encaissements_du_bulletin(b.id) if e.origine_id is None)
        assert total == 40.0


# ---------------------------------------------------------------------------
# Mise à jour d'une base passée par la PR #59, et comparaison avec un lot
# ---------------------------------------------------------------------------

def test_a_mise_a_jour_depuis_l_etat_exact_de_la_pr59(fresh_app):
    """Base au schéma de la PR #59 (b8d0f2a4c593), reprise déjà faite, un
    encaissement et une qualification postérieurs : la mise à jour classe
    les bulletins sans toucher à ce qui existe."""
    from flask_migrate import downgrade, upgrade
    from sqlalchemy import inspect, text
    from app.extensions import db
    with fresh_app.app_context():
        downgrade(revision="b8d0f2a4c593")
        assert "rapprochement_bulletin" not in inspect(db.engine).get_table_names()
        p = _personne(db)
        c = _cotisation(db, participant=p)
        _versement_ancien(db, c, 10.0, "cheque", "chèque saisi sur la fiche")
        b = _bulletin(db, participant=p, montant=30.0)
        sans_fiche = _bulletin(db, montant=12.0)
        db.session.commit()
        _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())   # la reprise publiée
        db.session.commit()
        from app.services.encaissements import enregistrer
        enregistrer(5.0, "especes", date_encaissement=date(ANNEE, 10, 1), inscription=sans_fiche)
        db.session.commit()
        bid, sans_fiche_id = b.id, sans_fiche.id
        avant = db.session.execute(text(
            "SELECT id, montant, mode, a_qualifier, hors_caisse, source_ancienne FROM encaissement ORDER BY id")).fetchall()
        db.session.remove()
        upgrade(revision="head")
        apres = db.session.execute(text(
            "SELECT id, montant, mode, a_qualifier, hors_caisse, source_ancienne FROM encaissement ORDER BY id")).fetchall()
        assert apres == avant                                   # rien créé, rien modifié
        assert _ligne(bid).classement == "a_rapprocher"
        assert _ligne(sans_fiche_id).classement == "suivi"
        # Retour arrière puis nouvelle mise à jour : même classement.
        db.session.remove()
        downgrade(revision="c1d3e5f7a902")
        upgrade(revision="head")
        assert _ligne(bid).classement == "a_rapprocher"


def test_a_valeur_d_origine_retrouvee_dans_une_sauvegarde(app, admin_client, tmp_path, monkeypatch):
    """Depuis la PR #59, ouvrir la liste des inscriptions recalcule la
    colonne du bulletin (miroir du total versé) : 30 € deviennent 10 €. La
    valeur d'origine est relue dans un lot antérieur, sans restauration."""
    from app.extensions import db
    from app.models import InscriptionAnnuelle, RapprochementBulletin
    from app.services import sauvegarde as svc
    from app.services.inscriptions_annuelles import resynchroniser_reglement
    from app.services.reprise_reglements import classer_tout
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: tmp_path)
    with app.app_context():
        uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if not uri.startswith("sqlite") and not svc._trouver_pg_dump():
            pytest.skip("pg_dump indisponible")
        p = _personne(db)
        c = _cotisation(db, participant=p)
        _versement_ancien(db, c, 10.0, "cheque")
        b = _bulletin(db, participant=p, montant=30.0)
        db.session.commit()
        lot = svc.creer_sauvegarde()["base"]                      # avant la mise à jour
        _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())
        resynchroniser_reglement(db.session.get(InscriptionAnnuelle, b.id))   # ouverture de la liste
        db.session.commit()
        assert db.session.get(InscriptionAnnuelle, b.id).reglement_montant == 10.0
        classer_tout(db.session.connection())
        db.session.commit()
        assert _ligne(b.id).classement == "reporte"               # 10 = total versé : rien de visible
        bid = b.id
    for _ in range(2):                                            # rejouable sans doublon
        r = admin_client.post("/caisse/rapprochement-bulletins", data={"action": "comparer", "base": lot},
                              follow_redirects=True)
        assert r.status_code == 200
    with app.app_context():
        [depuis_lot] = RapprochementBulletin.query.filter_by(inscription_annuelle_id=bid, source="sauvegarde").all()
        assert (depuis_lot.montant_origine, depuis_lot.classement, depuis_lot.source_detail) == (30.0, "a_rapprocher", lot)


def test_a_reprise_d_une_installation_ancienne(tmp_path):
    """Base synthétique au schéma d'une installation artisanale (révision
    de23fa45bc67, avant la caisse), puis toutes les migrations comme lors de
    la reprise par l'installateur : les 30 € ne disparaissent pas, le report
    prouvé n'est pas compté deux fois. (Même scénario que la recette Windows
    ``desktop/migration_smoke.py``, source PostgreSQL 18.1.)"""
    from datetime import datetime
    from flask import Flask
    from flask_migrate import upgrade
    from sqlalchemy import MetaData, Table, text
    import conftest
    from app.extensions import db, migrate
    from config import Config
    if conftest._SUR_POSTGRES:
        conftest._recreer_base("ancienne")
        url = conftest._url_de_base("ancienne").render_as_string(hide_password=False)
    else:
        url = "sqlite:///" + str(tmp_path / "ancienne.db")
    ancienne = Flask("installation-ancienne", instance_path=str(tmp_path / "instance"))
    ancienne.config.from_object(Config)
    ancienne.config["SQLALCHEMY_DATABASE_URI"] = url
    db.init_app(ancienne)
    migrate.init_app(ancienne, db, directory=str(RACINE / "migrations"))
    try:
        with ancienne.app_context():
            upgrade(directory=str(RACINE / "migrations"), revision="de23fa45bc67")
            with db.engine.begin() as connexion:
                metadata = MetaData()

                def inserer(nom_table, **valeurs):
                    table = Table(nom_table, metadata, autoload_with=connexion)
                    return connexion.execute(table.insert().values(
                        **{k: v for k, v in valeurs.items() if k in table.c}).returning(table.c.id)).scalar_one()

                maintenant = datetime(2024, 9, 1, 10, 0)
                pid = inserer("participant", nom="RECETTE", prenom="Bulletin", created_secteur="Familles",
                              type_public="H", droit_image_statut="non_renseigne", est_benevole=False,
                              statut_inscription="actif", created_at=maintenant, updated_at=maintenant)
                cid = inserer("cotisation", annee_scolaire=2024, type_cotisation="participation", participant_id=pid,
                              montant_du=60.0, date_reference=datetime(2024, 9, 1).date(), created_at=maintenant,
                              updated_at=maintenant)
                inserer("paiement", cotisation_id=cid, montant=10.0, mode="cheque",
                        date_paiement=datetime(2024, 9, 20).date(), commentaire="chèque saisi sur la fiche",
                        created_at=maintenant)
                distinct = inserer("inscription_annuelle", annee_scolaire=2024, nom="RECETTE", prenom="Bulletin",
                                   participant_id=pid, statut="validee", reglement_montant=30.0,
                                   reglement_mode="especes", reglement_date=datetime(2024, 9, 12).date(),
                                   date_inscription=datetime(2024, 9, 10).date(), created_at=maintenant,
                                   updated_at=maintenant)
                pid2 = inserer("participant", nom="RECETTE", prenom="Reporte", created_secteur="Familles",
                               type_public="H", droit_image_statut="non_renseigne", est_benevole=False,
                               statut_inscription="actif", created_at=maintenant, updated_at=maintenant)
                cid2 = inserer("cotisation", annee_scolaire=2024, type_cotisation="participation", participant_id=pid2,
                               montant_du=60.0, date_reference=datetime(2024, 9, 1).date(), created_at=maintenant,
                               updated_at=maintenant)
                inserer("paiement", cotisation_id=cid2, montant=45.0, mode="especes",
                        date_paiement=datetime(2024, 9, 12).date(), commentaire="Inscription annuelle 2024-2025",
                        created_at=maintenant)
                reporte = inserer("inscription_annuelle", annee_scolaire=2024, nom="RECETTE", prenom="Reporte",
                                  participant_id=pid2, statut="validee", reglement_montant=45.0,
                                  reglement_mode="especes", reglement_date=datetime(2024, 9, 12).date(),
                                  date_inscription=datetime(2024, 9, 10).date(), created_at=maintenant,
                                  updated_at=maintenant)
            upgrade(directory=str(RACINE / "migrations"), revision="head")
            with db.engine.connect() as connexion:
                lignes = {r[0]: r[1:] for r in connexion.execute(text(
                    "SELECT inscription_annuelle_id, classement, montant_origine, mode_origine, montant_ecart "
                    "FROM rapprochement_bulletin"))}
                assert lignes[distinct] == ("a_rapprocher", 30.0, "especes", 30.0)
                assert lignes[reporte] == ("reporte", 45.0, "especes", 0.0)
                total = connexion.execute(text("SELECT sum(montant) FROM encaissement")).scalar_one()
                assert total == 55.0                     # 10 + 45 : rien d'ajouté, rien compté deux fois
    finally:
        with ancienne.app_context():
            db.session.remove()
            db.engine.dispose()
        if conftest._SUR_POSTGRES:
            conftest._supprimer_base("ancienne")
