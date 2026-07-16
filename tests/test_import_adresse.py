"""Import d'habitants AVEC adresse/ville (pour la carte) + repli par commune."""
import io
import re
import uuid

from openpyxl import Workbook


def _classeur(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Habitants"
    ws.append(["NOMS", "PRENOMS", "ANNEE NAISSANCE", "SEXE", "VILLE", "ADRESSE", "QUARTIER"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parser_capture_adresse_ville():
    from app.services.import_participants import parser_feuille

    lignes = [
        ["NOMS", "PRENOMS", "ANNEE NAISSANCE", "SEXE", "VILLE", "ADRESSE", "QUARTIER"],
        ["DURAND", "Léa", 1990, "F", "Creil", "12 rue Victor Hugo", "Rouher"],
    ]
    personnes, raison = parser_feuille("F", lignes)
    assert raison is None
    p = personnes[0]
    assert p.adresse == "12 rue Victor Hugo"
    assert p.ville == "Creil"
    assert p.quartier == "Rouher"


def test_import_persiste_adresse_et_lie_quartier(app, admin_client):
    from app.extensions import db
    from app.models import Participant, Quartier

    pref = uuid.uuid4().hex[:6].upper()
    qnom = f"QX{pref}"
    with app.app_context():
        db.session.add(Quartier(ville="Creil", nom=qnom))
        db.session.commit()

    data = _classeur([[f"HAB{pref}", "Yanis", 1992, "M", "Creil", "5 rue Voltaire", qnom]])

    r = admin_client.post("/participants/import",
                          data={"fichier": (io.BytesIO(data), "habitants.xlsx")},
                          content_type="multipart/form-data")
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.get_data(as_text=True)).group(1)
    admin_client.post("/participants/import/confirmer", data={"token": token})

    with app.app_context():
        p = Participant.query.filter(Participant.nom.ilike(f"HAB{pref}")).first()
        assert p is not None
        assert p.adresse == "5 rue Voltaire"
        assert p.ville == "Creil"
        q = Quartier.query.filter_by(nom=qnom).first()
        assert p.quartier_id == q.id  # relié au quartier existant


def test_import_cree_quartier_et_qpv(app, admin_client):
    from app.extensions import db
    from app.models import Participant, Quartier

    pref = uuid.uuid4().hex[:6].upper()
    qnom = f"Rouher{pref}"  # quartier inexistant -> doit être créé, marqué QPV
    wb = Workbook()
    ws = wb.active
    ws.title = "Habitants"
    ws.append(["NOMS", "PRENOMS", "ANNEE NAISSANCE", "SEXE", "VILLE", "ADRESSE", "QUARTIER", "QPV"])
    ws.append([f"QPV{pref}", "Awa", 1995, "F", "Creil", "3 rue Henri Dunant", qnom, "oui"])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    r = admin_client.post("/participants/import",
                          data={"fichier": (io.BytesIO(data), "h.xlsx")},
                          content_type="multipart/form-data")
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.get_data(as_text=True)).group(1)
    admin_client.post("/participants/import/confirmer", data={"token": token})

    with app.app_context():
        q = Quartier.query.filter_by(ville="Creil", nom=qnom).first()
        assert q is not None and q.is_qpv is True  # quartier créé + QPV
        p = Participant.query.filter(Participant.nom.ilike(f"QPV{pref}")).first()
        assert p.quartier_id == q.id


def test_import_ne_cree_pas_quartier_commune(app, admin_client):
    """Sans colonne QUARTIER, on ne crée pas un quartier nommé comme la ville."""
    from app.extensions import db
    from app.models import Quartier

    pref = uuid.uuid4().hex[:6].upper()
    data = _classeur([[f"NOQ{pref}", "Sam", 1988, "M", "Creil", "1 rue Test", ""]])
    r = admin_client.post("/participants/import",
                          data={"fichier": (io.BytesIO(data), "h.xlsx")},
                          content_type="multipart/form-data")
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.get_data(as_text=True)).group(1)
    admin_client.post("/participants/import/confirmer", data={"token": token})
    with app.app_context():
        assert Quartier.query.filter_by(ville="Creil", nom="Creil").first() is None


def test_repartition_repli_par_commune(app):
    from app.extensions import db
    from app.models import Participant
    from app.services.cartographie import repartition_par_quartier

    ville = f"Commune-{uuid.uuid4().hex[:8]}"
    with app.app_context():
        # habitant localisé SANS quartier : doit être regroupé par sa commune
        p = Participant(nom="Loc", prenom="Ville", ville=ville, latitude=49.40, longitude=2.80)
        db.session.add(p)
        db.session.commit()

        res = repartition_par_quartier()
        grp = next((g for g in res["quartiers"] if g["nom"] == ville), None)
        assert grp is not None
        assert grp["id"] is None and grp["total"] >= 1
