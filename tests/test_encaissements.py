"""Circuit d'encaissement (audit C2, 2.2, 2.4, 2.5 et C1 résiduel).

Scénarios rejoués de l'audit C2 (argent reçu sur un bulletin avant la fiche) :
- 30 € en espèces : la caisse les voit tout de suite, une seule fois ;
- 20 € espèces + 50 € chèque : les modes restent distincts jusqu'au bordereau ;
- 80 € pour 70 € dus : les 10 € restent visibles (trop-perçu), jamais perdus ;
- 30 € sur le bulletin + 10 € saisis sur la fiche : rien ne disparaît ;
- régénérer les cotisations ne ventile jamais deux fois ;
- un double envoi du même formulaire n'enregistre qu'une somme ;
- un bulletin encaissé ne peut plus être supprimé.
Et : contre-passation motivée, règlements de location en caisse, fiches
supprimées dans la répartition, reprise idempotente des données anciennes,
anomalies NaN/infini traitées sans effacement arbitraire.
"""
import uuid
from datetime import date

import pytest

ANNEE = 2036  # campagne dédiée, avec son propre barème


def _vider(app):
    from app.extensions import db
    from app.models import CaisseMouvement, Don, Encaissement, Paiement
    with app.app_context():
        CaisseMouvement.query.delete()
        Paiement.query.delete()
        Encaissement.query.update({"origine_id": None})
        Encaissement.query.delete()
        Don.query.delete()
        db.session.commit()


@pytest.fixture()
def bareme(app):
    from app.extensions import db
    from app.models import TarifBareme
    with app.app_context():
        TarifBareme.query.filter_by(annee_scolaire=ANNEE).delete()
        for type_tarif, montant in (("adhesion_individuelle", 10.0), ("adhesion_familiale", 15.0),
                                    ("participation", 60.0)):
            db.session.add(TarifBareme(annee_scolaire=ANNEE, type_tarif=type_tarif, montant=montant,
                                       date_debut=date(2000, 1, 1)))
        db.session.commit()
    _vider(app)


def _bulletin(app, **extra):
    from app.extensions import db
    from app.models import InscriptionAnnuelle
    with app.app_context():
        i = InscriptionAnnuelle(annee_scolaire=ANNEE, nom=f"Caisse{uuid.uuid4().hex[:6]}", prenom="Test",
                                date_inscription=date(ANNEE, 9, 10), **extra)
        db.session.add(i)
        db.session.commit()
        return i.id


def _encaisser(app, bid, montant, mode, jour=None, jeton=None):
    from app.extensions import db
    from app.models import InscriptionAnnuelle
    from app.services.inscriptions_annuelles import encaisser
    with app.app_context():
        return encaisser(db.session.get(InscriptionAnnuelle, bid), montant, mode=mode,
                         date_paiement=jour or date(ANNEE, 9, 12), jeton=jeton)


def _creer_fiche(app, bid):
    from app.extensions import db
    from app.models import InscriptionAnnuelle
    from app.services.inscriptions_annuelles import creer_participant
    with app.app_context():
        participant, _ = creer_participant(db.session.get(InscriptionAnnuelle, bid), user_id=None,
                                           secteur="Adultes", inscrire_ateliers=False)
        return participant.id


def _caisse(app):
    from app.services.caisse import etat_caisse
    with app.app_context():
        e = etat_caisse()
        return e["encaissements_especes"], e["cheques_en_attente"]


def _versements(app, pid):
    from app.models import Cotisation, Paiement
    with app.app_context():
        return sorted(
            (p.mode, p.montant, p.date_paiement)
            for p in Paiement.query.join(Cotisation).filter(Cotisation.participant_id == pid).all()
        )


# ---------------------------------------------------------------------------
# C2 : argent reçu sur un bulletin avant la fiche
# ---------------------------------------------------------------------------

def test_especes_sur_bulletin_vues_en_caisse_une_seule_fois(app, bareme):
    bid = _bulletin(app)
    _encaisser(app, bid, 30, "especes")
    assert _caisse(app) == (30.0, 0.0)           # visible AVANT la fiche
    pid = _creer_fiche(app, bid)
    assert _caisse(app) == (30.0, 0.0)           # et pas une seconde fois après
    assert _versements(app, pid) == [("especes", 10.0, date(ANNEE, 9, 12)),
                                     ("especes", 20.0, date(ANNEE, 9, 12))]


def test_modes_melanges_conserves(app, bareme):
    bid = _bulletin(app)
    _encaisser(app, bid, 20, "especes")
    _encaisser(app, bid, 50, "cheque", jour=date(ANNEE, 9, 13))
    pid = _creer_fiche(app, bid)
    assert _caisse(app) == (20.0, 50.0)
    modes = {}
    for mode, montant, _ in _versements(app, pid):
        modes[mode] = round(modes.get(mode, 0) + montant, 2)
    assert modes == {"especes": 20.0, "cheque": 50.0}


def test_trop_percu_visible_jamais_perdu(app, bareme):
    from app.extensions import db
    from app.models import InscriptionAnnuelle
    from app.services.inscriptions_annuelles import etat_reglement
    bid = _bulletin(app)
    _encaisser(app, bid, 80, "especes")          # 70 € dus (10 + 60)
    _creer_fiche(app, bid)
    assert _caisse(app) == (80.0, 0.0)
    with app.app_context():
        etat = etat_reglement(db.session.get(InscriptionAnnuelle, bid))
        assert etat["regle"] == 70.0 and etat["statut"] == "complet"
        assert etat["trop_percu"] == 10.0


def test_versement_sur_fiche_n_efface_pas_le_bulletin(app, bareme, admin_client):
    """30 € sur le bulletin, puis 10 € saisis sur la fiche avant la
    régénération : les 30 € étaient perdus (le report s'arrêtait)."""
    from app.extensions import db
    from app.models import Cotisation, InscriptionAnnuelle
    from app.services.inscriptions_annuelles import generer_cotisations
    bid = _bulletin(app)
    _encaisser(app, bid, 30, "especes")
    pid = _creer_fiche(app, bid)
    with app.app_context():
        adhesion = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one()
        cid = adhesion.id
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "10", "mode": "cheque", "jeton": uuid.uuid4().hex})
    with app.app_context():
        generer_cotisations(db.session.get(InscriptionAnnuelle, bid))
        generer_cotisations(db.session.get(InscriptionAnnuelle, bid))   # rejouée : rien de plus
    total = round(sum(m for _, m, _ in _versements(app, pid)), 2)
    assert total == 40.0
    assert _caisse(app) == (30.0, 10.0)


def test_double_envoi_du_meme_formulaire(app, bareme):
    from app.models import Encaissement
    from app.services.inscriptions_annuelles import InscriptionAnnuelleErreur
    bid = _bulletin(app)
    jeton = uuid.uuid4().hex
    _encaisser(app, bid, 25, "especes", jeton=jeton)
    with pytest.raises(InscriptionAnnuelleErreur):
        _encaisser(app, bid, 25, "especes", jeton=jeton)
    with app.app_context():
        assert Encaissement.query.filter_by(inscription_annuelle_id=bid).count() == 1
    assert _caisse(app) == (25.0, 0.0)


def test_bulletin_encaisse_non_supprimable(app, bareme, admin_client):
    from app.models import InscriptionAnnuelle
    bid = _bulletin(app)
    _encaisser(app, bid, 15, "especes")
    admin_client.post(f"/inscriptions-annuelles/{bid}/supprimer")
    from app.extensions import db
    with app.app_context():
        assert db.session.get(InscriptionAnnuelle, bid) is not None


def test_montant_non_fini_refuse_a_l_encaissement(app, bareme):
    from app.services.inscriptions_annuelles import InscriptionAnnuelleErreur
    bid = _bulletin(app)
    for valeur in ("nan", "inf", "-5", "0"):
        with pytest.raises(InscriptionAnnuelleErreur):
            _encaisser(app, bid, valeur, "especes")


# ---------------------------------------------------------------------------
# 2.2 : contre-passation et ajustement motivés
# ---------------------------------------------------------------------------

def test_contre_passation_d_un_versement_errone(app, bareme, admin_client):
    from app.extensions import db
    from app.models import AuditLog, Cotisation, Encaissement
    bid = _bulletin(app)
    pid = _creer_fiche(app, bid)
    with app.app_context():
        cid = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one().id
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "300", "mode": "especes", "jeton": uuid.uuid4().hex})
    assert _caisse(app) == (300.0, 0.0)
    with app.app_context():
        versement = db.session.get(Cotisation, cid).paiements[0]
        vid = versement.id
    # Sans motif : refusé.
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement/{vid}/contrepasser", data={"motif": ""})
    assert _caisse(app) == (300.0, 0.0)
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement/{vid}/contrepasser",
                      data={"motif": "300 € saisis au lieu de 30 €", "jeton": uuid.uuid4().hex})
    assert _caisse(app) == (0.0, 0.0)
    # Une seconde fois : refusé.
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement/{vid}/contrepasser",
                      data={"motif": "encore", "jeton": uuid.uuid4().hex})
    with app.app_context():
        cotisation = db.session.get(Cotisation, cid)
        assert cotisation.montant_regle == 0.0 and cotisation.reste_du == 60.0
        ecritures = Encaissement.query.filter_by(participant_id=pid).order_by(Encaissement.id).all()
        assert [e.montant for e in ecritures] == [300.0, -300.0]
        assert ecritures[1].origine_id == ecritures[0].id and ecritures[1].created_by_user_id
        assert AuditLog.query.filter_by(action="encaissement.contre_passation").count() >= 1
    # La cotisation portant des versements (même annulés) n'est pas supprimable.
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/supprimer")
    with app.app_context():
        assert db.session.get(Cotisation, cid) is not None


def test_montant_du_ne_descend_pas_sous_le_paye(app, bareme, admin_client):
    from app.extensions import db
    from app.models import Cotisation
    bid = _bulletin(app)
    pid = _creer_fiche(app, bid)
    with app.app_context():
        cid = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one().id
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "40", "mode": "cheque", "jeton": uuid.uuid4().hex})
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/montant", data={"montant_du": "20"})
    with app.app_context():
        assert db.session.get(Cotisation, cid).montant_du == 60.0


def test_ajustement_manuel_motive(app, bareme, admin_client):
    from app.models import CaisseMouvement
    admin_client.post("/caisse/ajustement", data={"montant": "-12,40", "motif": ""})
    with app.app_context():
        assert CaisseMouvement.query.filter_by(type_mouvement="ajustement").count() == 0
    jeton = uuid.uuid4().hex
    for _ in range(2):  # double clic
        admin_client.post("/caisse/ajustement", data={"montant": "-12,40", "motif": "achat de piles", "jeton": jeton})
    with app.app_context():
        assert CaisseMouvement.query.filter_by(type_mouvement="ajustement").count() == 1
        from app.services.caisse import etat_caisse
        assert etat_caisse()["theorique_especes"] == -12.4


# ---------------------------------------------------------------------------
# 2.4 : locations de salles
# ---------------------------------------------------------------------------

def _reservation(app):
    from app.extensions import db
    from app.models import Espace, Preneur, Reservation, Site
    with app.app_context():
        suf = uuid.uuid4().hex[:6]
        site = Site(nom=f"Site {suf}", code=f"S{suf}")
        db.session.add(site)
        db.session.flush()
        espace = Espace(nom=f"Salle {suf}", site_id=site.id)
        preneur = Preneur(nom=f"Asso {suf}")
        db.session.add_all([espace, preneur])
        db.session.flush()
        r = Reservation(reference=f"R-{suf}", preneur_id=preneur.id, espace_id=espace.id,
                        titre="Réunion", montant_calcule=120.0, statut="confirmee")
        db.session.add(r)
        db.session.commit()
        return r.id


def test_reglement_de_location_entre_en_caisse(app, bareme, admin_client):
    from app.extensions import db
    from app.models import Reservation
    rid = _reservation(app)
    admin_client.post(f"/salles/reservation/{rid}/encaissement",
                      data={"montant": "40", "mode": "especes", "jeton": uuid.uuid4().hex})
    admin_client.post(f"/salles/reservation/{rid}/encaissement",
                      data={"montant": "80", "mode": "cheque", "jeton": uuid.uuid4().hex})
    assert _caisse(app) == (40.0, 80.0)
    with app.app_context():
        r = db.session.get(Reservation, rid)
        assert r.montant_regle == 120.0 and r.reste_du == 0.0
        eid = r.encaissements[1].id
    admin_client.post(f"/salles/reservation/{rid}/encaissement/{eid}/contrepasser",
                      data={"motif": "chèque sans provision", "jeton": uuid.uuid4().hex})
    assert _caisse(app) == (40.0, 0.0)
    with app.app_context():
        assert db.session.get(Reservation, rid).reste_du == 80.0
    admin_client.post(f"/salles/reservation/{rid}/supprimer")
    with app.app_context():
        assert db.session.get(Reservation, rid) is not None


# ---------------------------------------------------------------------------
# Suppression, anonymisation, répartition (2.5)
# ---------------------------------------------------------------------------

def test_suppression_de_fiche_conserve_caisse_et_repartition(app, bareme):
    from app.extensions import db
    from app.models import Encaissement, Participant
    from app.services.participant_suppression import supprimer_definitivement
    from app.services.prorata import FICHES_SUPPRIMEES, repartition
    ids = []
    for _ in range(2):
        bid = _bulletin(app)
        _encaisser(app, bid, 70, "especes", jeton=uuid.uuid4().hex)
        ids.append(_creer_fiche(app, bid))
    with app.app_context():
        avant = repartition(ANNEE)["totaux"]["regle"]
        supprimer_definitivement(db.session.get(Participant, ids[0]))
        db.session.commit()
        vue = repartition(ANNEE)
        assert vue["totaux"]["regle"] == avant
        assert vue["secteurs"][FICHES_SUPPRIMEES]["regle"] == 60.0
        assert all(e.participant_id is None or e.participant_id != ids[0] for e in Encaissement.query.all())
    assert _caisse(app) == (140.0, 0.0)


def test_anonymisation_efface_les_commentaires_de_reglement(app, bareme, admin_client):
    from app.extensions import db
    from app.models import Cotisation, Encaissement, Participant
    from app.services.purge_rgpd import anonymiser_participant
    bid = _bulletin(app)
    pid = _creer_fiche(app, bid)
    with app.app_context():
        cid = Cotisation.query.filter_by(participant_id=pid, type_cotisation="participation").one().id
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "60", "mode": "cheque", "commentaire": "chèque DUPONT 1234",
                            "jeton": uuid.uuid4().hex})
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        assert all(e.commentaire is None for e in Encaissement.query.filter_by(participant_id=pid))
        assert all(p.commentaire is None for p in db.session.get(Cotisation, cid).paiements)
        assert sum(e.montant for e in Encaissement.query.filter_by(participant_id=pid)) == 60.0


# ---------------------------------------------------------------------------
# Données anciennes : reprise idempotente, qualification, anomalies (C1)
# ---------------------------------------------------------------------------

def test_reprise_des_donnees_anciennes_idempotente(app, bareme, admin_client):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text
    from app.extensions import db
    from app.models import Encaissement, Paiement
    chemin = Path(__file__).resolve().parents[1] / "migrations/versions/b5d8e3a1f264_encaissements.py"
    spec = importlib.util.spec_from_file_location("migration_encaissements", chemin)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    bid = _bulletin(app)
    rid = _reservation(app)
    with app.app_context():
        # État d'une ancienne version : versement sans encaissement, argent
        # noté sur un bulletin sans fiche, location soldée « par date ».
        from app.models import Cotisation
        c = Cotisation(annee_scolaire=ANNEE, type_cotisation="participation", montant_du=60,
                       date_reference=date(ANNEE, 9, 1))
        db.session.add(c)
        db.session.commit()
        db.session.execute(text("INSERT INTO paiement (cotisation_id, montant, date_paiement, mode, created_at) "
                                "VALUES (:c, 45, :d, 'cheque', :t)"), {"c": c.id, "d": date(ANNEE, 9, 2),
                                                                        "t": date(ANNEE, 9, 2)})
        db.session.execute(text("UPDATE inscription_annuelle SET reglement_montant = 35, reglement_mode = 'especes' "
                                "WHERE id = :b"), {"b": bid})
        db.session.execute(text("UPDATE reservation SET acompte_montant = 50, acompte_regle_le = :a, "
                                "solde_regle_le = :s WHERE id = :r"),
                           {"a": date(ANNEE, 9, 3), "s": date(ANNEE, 9, 20), "r": rid})
        db.session.commit()
        for _ in range(2):  # rejouée : aucun doublon
            migration.reprendre_donnees(db.session.connection())
            db.session.commit()
        versement = Paiement.query.filter_by(cotisation_id=c.id).one()
        sources = [f"paiement:{versement.id}", f"bulletin:{bid}", f"reservation-acompte:{rid}", f"reservation-solde:{rid}"]
        assert Encaissement.query.filter(Encaissement.source_ancienne.in_(sources)).count() == 4
        assert versement.encaissement_id is not None
        bulletin = Encaissement.query.filter_by(source_ancienne=f"bulletin:{bid}").one()
        assert bulletin.a_qualifier and bulletin.hors_caisse and bulletin.mode == "inconnu"
        assert "dernier mode saisi : espèces" in bulletin.note_qualification
        solde = Encaissement.query.filter_by(source_ancienne=f"reservation-solde:{rid}").one()
        assert solde.montant == 70.0 and solde.a_qualifier
        eid = bulletin.id
    # Le versement ancien compte en caisse ; les sommes à qualifier non.
    assert _caisse(app) == (0.0, 45.0)
    page = admin_client.get("/caisse/a-qualifier").get_data(as_text=True)
    assert "35.00 €" in page
    # Qualification : 20 € espèces + 15 € chèque, argent jamais compté.
    admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes", "montant_0": "20",
                                                   "mode_1": "cheque", "montant_1": "15"})   # sans choix : refusé
    assert _caisse(app) == (0.0, 45.0)
    admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes", "montant_0": "20",
                                                   "mode_1": "cheque", "montant_1": "10", "caisse": "dans"})  # 30 ≠ 35
    assert _caisse(app) == (0.0, 45.0)
    admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes", "montant_0": "20",
                                                   "mode_1": "cheque", "montant_1": "15", "caisse": "dans"})
    assert _caisse(app) == (20.0, 60.0)
    # La fiche créée ensuite reçoit l'argent qualifié, mode par mode.
    pid = _creer_fiche(app, bid)
    modes = sorted({m for m, _, _ in _versements(app, pid)})
    assert modes == ["cheque", "especes"]
    assert _caisse(app) == (20.0, 60.0)


def test_anomalie_de_montant_corrigee_sans_effacement(app, bareme, admin_client):
    from sqlalchemy import text
    from app.extensions import db
    from app.models import AuditLog, Cotisation
    from app.services.anomalies_montants import lister
    with app.app_context():
        c = Cotisation(annee_scolaire=ANNEE, type_cotisation="participation", montant_du=60,
                       date_reference=date(ANNEE, 9, 1))
        db.session.add(c)
        db.session.commit()
        postgres = db.engine.dialect.name == "postgresql"
        if postgres:
            # Base ancienne : la migration n'a pas pu poser la contrainte.
            db.session.execute(text("ALTER TABLE paiement DROP CONSTRAINT IF EXISTS ck_paiement_montant_fini"))
            db.session.commit()
        valeur = "'NaN'::float8" if postgres else "9e999"
        db.session.execute(text(f"INSERT INTO paiement (cotisation_id, montant, date_paiement, mode, created_at) "
                                f"VALUES (:c, {valeur}, :d, 'especes', :d)"), {"c": c.id, "d": date(ANNEE, 9, 2)})
        db.session.commit()
        anomalies = [a for a in lister() if a.table == "paiement"]
        assert len(anomalies) == 1
        ligne = anomalies[0].ligne_id
    from app.services.caisse import etat_caisse
    with app.app_context():
        import math
        assert math.isfinite(etat_caisse()["theorique_especes"])
    admin_client.post("/controle/anomalies-montants", data={"table": "paiement", "colonne": "montant",
                                                            "ligne_id": ligne, "montant": "15", "motif": ""})
    with app.app_context():
        assert any(a.ligne_id == ligne for a in lister())
    admin_client.post("/controle/anomalies-montants", data={"table": "paiement", "colonne": "montant",
                                                            "ligne_id": ligne, "montant": "15",
                                                            "motif": "reçu papier n° 88"})
    with app.app_context():
        assert not any(a.ligne_id == ligne and a.table == "paiement" for a in lister())
        trace = AuditLog.query.filter_by(action="montant.anomalie_corrigee").order_by(AuditLog.id.desc()).first()
        assert "reçu papier n° 88" in trace.details and ("nan" in trace.details.lower() or "inf" in trace.details.lower())
        if postgres:  # la contrainte est reposée dès que les données le permettent
            assert db.session.execute(text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'ck_paiement_montant_fini'")).scalar() == 1
    assert _caisse(app)[0] == 15.0


def test_encaissements_simultanes_sur_un_bulletin(app, bareme, dialecte):
    """Audit C2 : « deux encaissements simultanés, un seul retenu ». Les deux
    sommes sont conservées, et leur ventilation ne dépasse jamais le dû."""
    if dialecte != "postgresql":
        pytest.skip("concurrence réelle : PostgreSQL seulement")
    import threading
    from app.extensions import db
    from app.models import Encaissement, InscriptionAnnuelle
    from app.services.inscriptions_annuelles import etat_reglement
    bid = _bulletin(app)
    _creer_fiche(app, bid)
    depart = threading.Barrier(2)
    erreurs = []

    def payer():
        try:
            depart.wait()
            _encaisser(app, bid, 50, "especes", jeton=uuid.uuid4().hex)
        except Exception as exc:  # pragma: no cover - affiché en cas d'échec
            erreurs.append(exc)

    fils = [threading.Thread(target=payer) for _ in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert not erreurs
    with app.app_context():
        assert Encaissement.query.filter_by(inscription_annuelle_id=bid).count() == 2
        etat = etat_reglement(db.session.get(InscriptionAnnuelle, bid))
        assert etat["regle"] == 70.0 and etat["trop_percu"] == 30.0
    assert _caisse(app) == (100.0, 0.0)


# ---------------------------------------------------------------------------
# 2.1 : factures figées et avoirs ; mineurs caisse
# ---------------------------------------------------------------------------

def _texte_docx(document):
    return "\n".join(p.text for p in document.paragraphs) + "\n".join(
        c.text for t in document.tables for r in t.rows for c in r.cells)


def test_facture_figee_puis_avoir_a_l_annulation(app, bareme, admin_client, monkeypatch):
    import json
    from app.extensions import db
    from app.models import Reservation
    from app.services import documents_salles as docs
    from app.salles import locations
    monkeypatch.setattr(locations, "bloquants", lambda r: [])
    monkeypatch.setattr(docs, "ecrire", lambda document, dossier, nom, pdf=True: (_ecrire_docx(document, dossier, nom), None))
    rid = _reservation(app)
    admin_client.post(f"/salles/reservation/{rid}/document/facture")
    with app.app_context():
        r = db.session.get(Reservation, rid)
        numero = r.facture_numero
        assert numero and r.facture_snapshot_json
        assert json.loads(r.facture_snapshot_json)["total"] == 120.0
        # Prix modifié après émission : refusé, et la facture ne change pas.
        r.montant_calcule = 60.0
        db.session.commit()
    admin_client.post(f"/salles/reservation/{rid}/reglements", data={"montant_manuel": "60", "motif_montant_manuel": "remise"})
    with app.app_context():
        r = db.session.get(Reservation, rid)
        assert r.montant_manuel is None and r.montant_du == 120.0
        texte = _texte_docx(docs.facture(r, numero, ""))
        assert "120.00 €" in texte and "60.00" not in texte
    # Annulation : un avoir numéroté, et la facture se réimprime « annulée ».
    admin_client.post(f"/salles/reservation/{rid}/statut", data={"statut": "annulee", "motif": "désistement"})
    with app.app_context():
        r = db.session.get(Reservation, rid)
        assert r.avoir_numero and r.avoir_numero.startswith("AV-")
        assert r.montant_du == 0.0
        assert "ANNULÉE" in _texte_docx(docs.facture(r, numero, ""))
        assert "-120.00 €" in _texte_docx(docs.avoir(r)) and numero in _texte_docx(docs.avoir(r))
    # Réactivation refusée : l'avoir reste au registre.
    admin_client.post(f"/salles/reservation/{rid}/statut", data={"statut": "confirmee"})
    with app.app_context():
        assert db.session.get(Reservation, rid).statut == "annulee"


def _ecrire_docx(document, dossier, nom):
    import os
    chemin = os.path.join(dossier, nom + ".docx")
    document.save(chemin)
    return chemin


def test_annulation_d_un_don_en_especes_sans_reecriture(app, bareme, admin_client):
    from app.extensions import db
    from app.models import Don
    with app.app_context():
        d = Don(numero=f"T-{uuid.uuid4().hex[:6]}", annee=ANNEE, donateur_nom="Donateur", montant=50,
                date_don=date(ANNEE, 9, 1), forme_don="numeraire", mode_versement="especes")
        db.session.add(d)
        db.session.commit()
        did = d.id
    admin_client.post("/caisse/depot", data={"montant_especes": "50", "jeton": uuid.uuid4().hex})
    from app.services.caisse import etat_caisse
    with app.app_context():
        assert etat_caisse()["theorique_especes"] == 0.0
    admin_client.post(f"/dons/{did}/annuler", data={"motif": ""})
    with app.app_context():
        assert db.session.get(Don, did).est_annule is False
    admin_client.post(f"/dons/{did}/annuler", data={"motif": "chèque rendu au donateur"})
    with app.app_context():
        e = etat_caisse()
        # L'encaissement d'origine et le dépôt restent ; la correction est datée du jour.
        assert e["encaissements_especes"] == 50.0 and e["depots_especes"] == 50.0
        assert e["theorique_especes"] == -50.0
        assert db.session.get(Don, did).annulation_mouvement_id is not None


def test_numeros_jamais_reattribues_apres_restauration(app, bareme):
    from app.extensions import db
    from app.models import FinancialSequence
    from app.services.financial_sequence import next_number
    espace = f"test:{uuid.uuid4().hex[:6]}"
    with app.app_context():
        assert next_number(espace, []) == 1
        assert next_number(espace, []) == 2
        db.session.commit()
        # Restauration d'une sauvegarde antérieure : le compteur en base recule.
        FinancialSequence.query.filter_by(namespace=espace).delete()
        db.session.commit()
        assert next_number(espace, []) == 3
