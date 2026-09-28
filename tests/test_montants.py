"""Montants saisis : NaN, infini et bornes refusés partout (audit C1, 2.3).

Un « nan » tapé comme versement passait ``montant <= 0`` (faux pour NaN) :
caisse théorique NaN pour toujours, cotisation « soldée », reçu fiscal pour
un don infini. Ces tests rejouent exactement ces scénarios.
"""
import datetime as dt
import math
import uuid

import pytest

from app.utils.montants import NombreNonFini, lire_decimal, nombre_fini, parse_montant

VALEURS_NON_FINIES = ["nan", "NaN", "-nan", "inf", "-inf", "Infinity", "sNaN", "1e999"]


@pytest.mark.parametrize("brut", VALEURS_NON_FINIES)
def test_parse_montant_refuse_non_finis(brut):
    assert parse_montant(brut) is None
    assert parse_montant(brut, 0.0) == 0.0
    assert nombre_fini(brut) is None


@pytest.mark.parametrize("brut, attendu", [
    ("155", 155.0), ("155,5", 155.5), ("1 255,50 €", 1255.5), ("1 255.50", 1255.5),
    ("0,005", 0.01), ("12.344", 12.34),
])
def test_parse_montant_saisies_humaines(brut, attendu):
    assert parse_montant(brut) == attendu


def test_parse_montant_signe_et_bornes():
    assert parse_montant("-5") is None
    assert parse_montant("-5", negatif=True) == -5.0
    assert parse_montant("1000000") == 1000000.0
    assert parse_montant("1000000.01") is None
    assert parse_montant("") is None and parse_montant(None, 3.0) == 3.0
    assert parse_montant("abc") is None
    assert lire_decimal("12,5") is not None


def _vider(app):
    from app.extensions import db
    from app.models import CaisseMouvement, Don, Encaissement, Paiement
    with app.app_context():
        CaisseMouvement.query.delete()
        Paiement.query.delete()
        Encaissement.query.delete()
        Don.query.delete()
        db.session.commit()


def _cotisation(app, montant_du=20.0):
    from app.extensions import db
    from app.models import Cotisation, Participant
    from app.services.cotisations import annee_scolaire_courante
    with app.app_context():
        p = Participant(nom=f"Montant{uuid.uuid4().hex[:6]}", prenom="Test")
        db.session.add(p)
        db.session.flush()
        c = Cotisation(annee_scolaire=annee_scolaire_courante(), type_cotisation="participation",
                       participant_id=p.id, montant_du=montant_du, date_reference=dt.date.today())
        db.session.add(c)
        db.session.commit()
        return p.id, c.id


@pytest.mark.parametrize("brut", ["nan", "inf", "-inf"])
def test_versement_non_fini_refuse(app, admin_client, brut):
    _vider(app)
    pid, cid = _cotisation(app)
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": brut, "mode": "especes"})
    from app.models import Cotisation, Paiement
    from app.services.caisse import etat_caisse
    with app.app_context():
        assert Paiement.query.filter_by(cotisation_id=cid).count() == 0
        assert not math.isnan(etat_caisse()["theorique_especes"])
        from app.extensions import db
        assert db.session.get(Cotisation, cid).montant_du == 20.0


def test_montant_du_non_fini_refuse(app, admin_client):
    pid, cid = _cotisation(app, 20.0)
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/montant", data={"montant_du": "nan"})
    from app.extensions import db
    from app.models import Cotisation
    with app.app_context():
        assert db.session.get(Cotisation, cid).montant_du == 20.0


def test_don_infini_refuse_sans_recu(app, admin_client):
    _vider(app)
    admin_client.post("/dons/nouveau", data={
        "donateur_nom": "Mécène Infini", "montant": "inf", "forme_don": "numeraire",
        "mode_versement": "especes", "date_don": dt.date.today().isoformat(),
    })
    from app.models import Don
    with app.app_context():
        assert Don.query.filter_by(donateur_nom="Mécène Infini").count() == 0


@pytest.mark.parametrize("route, champ", [
    ("/caisse/fond", "montant"),
    ("/caisse/comptage", "montant_constate"),
])
def test_caisse_fond_et_comptage_non_finis(app, admin_client, route, champ):
    _vider(app)
    admin_client.post(route, data={champ: "nan"})
    from app.models import CaisseMouvement
    with app.app_context():
        assert CaisseMouvement.query.count() == 0


def test_depot_non_fini_ignore(app, admin_client):
    _vider(app)
    admin_client.post("/caisse/fond", data={"montant": "50"})
    admin_client.post("/caisse/depot", data={"montant_especes": "nan", "montant_cheques": "inf",
                                             "nb_cheques": "1"})
    from app.models import CaisseMouvement
    with app.app_context():
        assert CaisseMouvement.query.filter_by(type_mouvement="depot").count() == 0


def test_depot_cheques_borne_aux_cheques_en_attente(app, admin_client):
    """Audit 2.3 : on ne dépose pas plus de chèques qu'il n'y en a en attente."""
    _vider(app)
    pid, cid = _cotisation(app, 40.0)
    admin_client.post(f"/participants/{pid}/cotisation/{cid}/versement",
                      data={"montant": "40", "mode": "cheque"})
    r = admin_client.post("/caisse/depot", data={"montant_cheques": "90", "nb_cheques": "2"},
                          follow_redirects=True)
    assert "en attente de dépôt" in r.get_data(as_text=True)
    from app.models import CaisseMouvement
    with app.app_context():
        assert CaisseMouvement.query.filter_by(type_mouvement="depot").count() == 0
    r = admin_client.post("/caisse/depot", data={"montant_cheques": "40", "nb_cheques": "1"})
    assert "/bordereau" in r.headers["Location"]


def test_filet_base_refuse_nan_quel_que_soit_le_chemin(app):
    """Même un code qui oublierait parse_montant ne peut plus écrire NaN."""
    from app.extensions import db
    from app.models import CaisseMouvement
    with app.app_context():
        db.session.add(CaisseMouvement(type_mouvement="fond", canal="especes",
                                       montant=float("nan"), date_mouvement=dt.date.today()))
        with pytest.raises(NombreNonFini):
            db.session.flush()
        db.session.rollback()


def test_filet_base_renvoie_un_message_et_pas_une_erreur_500(app):
    """Une valeur non finie venue d'un chemin non protégé : message, rien d'écrit."""
    with app.test_request_context("/x", method="POST", headers={"Referer": "http://localhost/caisse"}):
        handler = app._find_error_handler(NombreNonFini("test"), [])
        assert handler is not None
        reponse, code = handler(NombreNonFini("test"))
        assert code == 303
        assert reponse.headers["Location"].endswith("/caisse")


def test_questionnaire_kiosque_note_non_finie_ignoree():
    assert nombre_fini("nan") is None and nombre_fini("4") == 4.0


# ---------------------------------------------------------------------------
# PostgreSQL : contrainte en base et verrou de caisse
# ---------------------------------------------------------------------------

def test_contrainte_base_refuse_nan_sur_postgresql(app, dialecte):
    if dialecte != "postgresql":
        pytest.skip("contrainte posée sur PostgreSQL seulement")
    from sqlalchemy.exc import IntegrityError
    from app.extensions import db
    with app.app_context():
        insertion = ("INSERT INTO caisse_mouvement (type_mouvement, canal, montant, date_mouvement, created_at) "
                     "VALUES ('fond', 'especes', CAST(:v AS float8), CURRENT_DATE, now())")
        db.session.execute(db.text(insertion), {"v": "12.5"})  # témoin : l'insertion elle-même est valide
        db.session.rollback()
        for valeur in ("NaN", "Infinity", "-Infinity"):
            with pytest.raises(IntegrityError, match="ck_caisse_mouvement_montant_fini"):
                db.session.execute(db.text(insertion), {"v": valeur})
            db.session.rollback()


def test_double_depot_simultane_ne_rend_pas_la_caisse_negative(app, dialecte):
    """Audit 2.3 : deux dépôts de toute la recette envoyés en même temps."""
    if dialecte != "postgresql":
        pytest.skip("concurrence réelle : PostgreSQL seulement")
    import threading
    from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    _vider(app)
    clients = []
    for _ in range(2):
        c = app.test_client()
        c.post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        clients.append(c)
    clients[0].post("/caisse/fond", data={"montant": "0"})
    pid, cid = _cotisation(app, 150.0)
    clients[0].post(f"/participants/{pid}/cotisation/{cid}/versement",
                    data={"montant": "150", "mode": "especes"})

    depart = threading.Barrier(2)

    def deposer(c):
        depart.wait()
        c.post("/caisse/depot", data={"montant_especes": "150"})

    fils = [threading.Thread(target=deposer, args=(c,)) for c in clients]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    from app.models import CaisseMouvement
    from app.services.caisse import etat_caisse
    with app.app_context():
        assert CaisseMouvement.query.filter_by(type_mouvement="depot").count() == 1
        assert etat_caisse()["theorique_especes"] == 0.0
