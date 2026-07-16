"""Import CSV de partenaires (tous les champs d'identification, upsert par nom)."""
import io
import json
import uuid


def test_importer_csv_cree_tous_les_champs(app):
    from app.extensions import db
    from app.models import Partenaire
    from app.partenaires.import_csv import importer_csv

    nom = f"Asso {uuid.uuid4().hex[:6]}"
    csv = (
        "nom;contact_nom;contact_prenom;adresse;email_contact;email_general;tel_contact;tel_general;"
        "secteurs;competences;territoire_couvert;modalites_orientation;niveau_orientation;description\n"
        f"{nom};Dupont;Marie;1 rue de Paris, 60100 Creil;m@a.fr;c@a.fr;;03 44 00 00 00;"
        "Numérique|Familles;numerique|emploi;Creil;Sur RDV;principal;Une description\n"
    ).encode("utf-8")

    with app.app_context():
        resume = importer_csv(csv)
        assert resume["crees"] == 1 and resume["maj"] == 0 and not resume["erreurs"]

        p = Partenaire.query.filter(db.func.lower(Partenaire.nom) == nom.lower()).first()
        assert p is not None
        assert p.contact_nom == "Dupont" and p.contact_prenom == "Marie"
        assert p.adresse == "1 rue de Paris, 60100 Creil"
        assert p.email_general == "c@a.fr" and p.tel_general == "03 44 00 00 00"
        assert p.niveau_orientation == "principal"
        assert set(json.loads(p.competences_orientation_json)) == {"numerique", "emploi"}
        assert {s.secteur for s in p.secteurs} == {"Numérique", "Familles"}


def test_importer_csv_upsert_par_nom(app):
    from app.extensions import db
    from app.models import Partenaire
    from app.partenaires.import_csv import importer_csv

    nom = f"Upsert {uuid.uuid4().hex[:6]}"
    with app.app_context():
        importer_csv(f"nom;tel_general\n{nom};0102030405\n".encode("utf-8"))
        # 2e import : même nom (casse différente) -> mise à jour, pas de doublon
        resume = importer_csv(f"nom;tel_general;adresse\n{nom.upper()};0600000000;Creil\n".encode("utf-8"))
        assert resume["maj"] == 1 and resume["crees"] == 0

        rows = Partenaire.query.filter(db.func.lower(Partenaire.nom) == nom.lower()).all()
        assert len(rows) == 1
        assert rows[0].tel_general == "0600000000"
        assert rows[0].adresse == "Creil"


def test_importer_csv_nom_manquant_continue(app):
    from app.extensions import db
    from app.models import Partenaire
    from app.partenaires.import_csv import importer_csv

    ok = f"Ok {uuid.uuid4().hex[:6]}"
    with app.app_context():
        resume = importer_csv(f"nom;adresse\n;ligne sans nom\n{ok};Creil\n".encode("utf-8"))
        assert resume["crees"] == 1
        assert resume["ignores"] == 1
        assert any("nom manquant" in e for e in resume["erreurs"])
        assert Partenaire.query.filter(db.func.lower(Partenaire.nom) == ok.lower()).first() is not None


def test_importer_csv_libelle_competence_vers_code(app):
    from app.partenaires.import_csv import _resoudre_competences

    with app.app_context():
        # un libellé humain doit retomber sur son code
        assert "aide_alimentaire" in _resoudre_competences(["Aide alimentaire"])
        assert _resoudre_competences(["valeur inconnue zzz"]) == []


def test_importer_csv_separateur_virgule_et_bom(app):
    from app.extensions import db
    from app.models import Partenaire
    from app.partenaires.import_csv import importer_csv

    nom = f"Virg {uuid.uuid4().hex[:6]}"
    # séparateur virgule + BOM Excel
    contenu = ("﻿nom,tel_general\n" + f"{nom},0123456789\n").encode("utf-8")
    with app.app_context():
        resume = importer_csv(contenu)
        assert resume["crees"] == 1
        assert Partenaire.query.filter(db.func.lower(Partenaire.nom) == nom.lower()).first().tel_general == "0123456789"


def test_page_import_et_modele(admin_client):
    r = admin_client.get("/partenaires/import")
    assert r.status_code == 200
    assert "Importer des partenaires" in r.get_data(as_text=True)

    m = admin_client.get("/partenaires/import/modele.csv")
    assert m.status_code == 200
    assert "text/csv" in m.headers["Content-Type"]
    assert "nom;contact_nom" in m.get_data(as_text=True)


def test_post_import_fichier(admin_client, app):
    from app.extensions import db
    from app.models import Partenaire

    nom = f"Post {uuid.uuid4().hex[:6]}"
    data = {
        "fichier": (io.BytesIO(f"nom;adresse\n{nom};10 rue Marcel Philippe, 60100 Creil\n".encode("utf-8")),
                    "partenaires.csv"),
    }
    r = admin_client.post("/partenaires/import", data=data,
                          content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Partenaire.query.filter(db.func.lower(Partenaire.nom) == nom.lower()).first() is not None
