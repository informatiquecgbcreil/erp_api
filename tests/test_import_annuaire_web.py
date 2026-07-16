"""Tests d'intégration de l'import d'annuaire (upload -> simulation -> création).

Construit un vrai classeur .xlsx en mémoire et le pousse via le client HTTP,
pour vérifier le flux complet et surtout l'absence de doublons (idempotence).
"""
import io
import uuid

import pytest
from openpyxl import Workbook


def _classeur(lignes_par_feuille: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    entete = ["NOMS", "PRENOMS", "ANNEE NAISSANCE", "AGE", "SEXE", "QUARTIER"]
    for nom_feuille, lignes in lignes_par_feuille.items():
        ws = wb.create_sheet(title=nom_feuille[:31])
        ws.append(entete)
        for l in lignes:
            ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def fichier_demo():
    pref = uuid.uuid4().hex[:6].upper()
    data = _classeur({
        "ZUMBA": [
            [f"DUPONT{pref}", "Marie", 1980, 44, "F", "ROUHER"],
            [f"MARTIN{pref}", "Paul", 1975, 49, "M", "CAVEE"],
            [f"SANSANNEE{pref}", "Lea", "ADULTE", "", "F", "INCONNU"],
        ],
        "YOGA": [
            [f"dupont{pref}", "MARIE", 1980, 44, "F", "ROUHER"],   # doublon de Marie
            [f"NOUVEAU{pref}", "Jean", 1990, 34, "M", "BAS DE CREIL"],
        ],
    })
    return data, pref


def test_acces_refuse_sans_scope_global(app):
    """Un responsable de secteur (sans portée globale) ne peut pas importer."""
    import uuid as _u
    email = f"resp-{_u.uuid4().hex[:6]}@example.org"
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User(email=email, nom="Resp", secteur_assigne="Familles")
        u.set_password("motdepasse-tests")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": "motdepasse-tests"})
    assert c.get("/participants/import").status_code == 403


def test_page_upload_visible_admin(admin_client):
    r = admin_client.get("/participants/import")
    assert r.status_code == 200
    assert "Importer un annuaire" in r.get_data(as_text=True)


def test_simulation_affiche_les_compteurs(admin_client, fichier_demo):
    data, pref = fichier_demo
    r = admin_client.post(
        "/participants/import",
        data={"fichier": (io.BytesIO(data), "stats.xlsx")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Résultat de la simulation" in page
    assert "Créer les fiches" in page
    # 3 fiables (Dupont, Martin, Nouveau), 1 doublon (dupont), 1 ambigu (sans année)
    assert f"DUPONT{pref}" in page


def test_creation_reelle_et_idempotence(app, admin_client, fichier_demo):
    data, pref = fichier_demo

    def compte():
        with app.app_context():
            from app.models import Participant
            return Participant.query.filter(Participant.nom.ilike(f"%{pref}%")).count()

    assert compte() == 0

    # 1) Simulation pour obtenir le token
    r = admin_client.post(
        "/participants/import",
        data={"fichier": (io.BytesIO(data), "stats.xlsx")},
        content_type="multipart/form-data",
    )
    page = r.get_data(as_text=True)
    import re
    m = re.search(r'name="token" value="([0-9a-f]{32})"', page)
    assert m, "le token d'import doit être présent"
    token = m.group(1)

    # 2) Confirmation SANS les ambigus
    r = admin_client.post("/participants/import/confirmer",
                          data={"token": token, "inclure_ambigus": ""})
    assert r.status_code == 302
    # 3 fiables créés ; le doublon interne (dupont) fusionné ; l'ambigu exclu
    assert compte() == 3

    with app.app_context():
        from app.models import Participant
        marie = Participant.query.filter(Participant.nom.ilike(f"DUPONT{pref}")).all()
        assert len(marie) == 1, "Marie ne doit exister qu'une fois malgré 2 lignes"
        assert marie[0].date_naissance is not None and marie[0].date_naissance.year == 1980
        assert marie[0].genre == "Femme"

    # 3) Idempotence : ré-importer le MÊME fichier ne crée aucun doublon
    r = admin_client.post(
        "/participants/import",
        data={"fichier": (io.BytesIO(data), "stats.xlsx")},
        content_type="multipart/form-data",
    )
    token2 = re.search(r'name="token" value="([0-9a-f]{32})"', r.get_data(as_text=True)).group(1)
    admin_client.post("/participants/import/confirmer", data={"token": token2})
    assert compte() == 3, "un second import ne doit RIEN recréer"


def test_inclure_ambigus_optionnel(app, admin_client, fichier_demo):
    data, pref = fichier_demo
    r = admin_client.post(
        "/participants/import",
        data={"fichier": (io.BytesIO(data), "stats.xlsx")},
        content_type="multipart/form-data",
    )
    import re
    token = re.search(r'name="token" value="([0-9a-f]{32})"', r.get_data(as_text=True)).group(1)
    admin_client.post("/participants/import/confirmer",
                      data={"token": token, "inclure_ambigus": "1"})
    with app.app_context():
        from app.models import Participant
        # 3 fiables + 1 ambigu = 4
        assert Participant.query.filter(Participant.nom.ilike(f"%{pref}%")).count() == 4
        ambigu = Participant.query.filter(Participant.nom.ilike(f"SANSANNEE{pref}")).first()
        assert ambigu is not None and ambigu.date_naissance is None


def test_token_invalide_refuse(admin_client):
    r = admin_client.post("/participants/import/confirmer", data={"token": "../../etc/passwd"})
    assert r.status_code == 302  # redirigé avec message, pas d'erreur 500


def test_fichier_non_xlsx_refuse(admin_client):
    r = admin_client.post(
        "/participants/import",
        data={"fichier": (io.BytesIO(b"pas un excel"), "donnees.txt")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert "Format non reconnu" in r.get_data(as_text=True)
