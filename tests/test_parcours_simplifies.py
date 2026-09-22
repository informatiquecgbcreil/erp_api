"""Parcours simplifiés : socle toujours actif, adhésions séparées des finances,
profil « Animation et accueil », rôles métier, page explicative."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules_actifs(app):
    """Pose une sélection de modules le temps d'un test, puis la rétablit."""
    from app.extensions import db
    from app.models import InstanceSettings

    etat = {}

    def poser(codes):
        with app.app_context():
            row = InstanceSettings.query.first()
            if row is None:
                row = InstanceSettings()
                db.session.add(row)
                etat.setdefault("cree", True)
            etat.setdefault("avant", row.enabled_modules_json)
            row.enabled_modules_json = json.dumps(codes)
            db.session.commit()

    yield poser
    with app.app_context():
        row = InstanceSettings.query.first()
        if row is not None and "avant" in etat:
            row.enabled_modules_json = etat["avant"]
            db.session.commit()


def test_catalogue_et_profils_coherents():
    from app.services.modules import CATALOG, PROFILES, PROFILE_LABELS, SOCLE

    assert SOCLE == "presences" and SOCLE in CATALOG
    assert "adhesions" in CATALOG
    assert set(PROFILES) == set(PROFILE_LABELS)
    for codes in PROFILES.values():
        assert set(codes) <= set(CATALOG)
        assert SOCLE in codes
    # « Animation et accueil » : adhésions et caisse, jamais les finances.
    assert "adhesions" in PROFILES["animation"] and "finances" not in PROFILES["animation"]
    assert set(PROFILES["essentiel"]) < set(PROFILES["animation"])


def test_profils_identiques_dans_l_assistant_windows():
    """L'assistant C# reprend les mêmes profils que l'application."""
    from app.services.modules import CATALOG, PROFILES

    source = (Path(__file__).resolve().parents[1] / "desktop" / "MonCentreSocial.cs").read_text(encoding="utf-8")
    for code in CATALOG:
        assert f'"{code}"' in source, code
    for nom, variable in (("essentiel", "profilEssentiel"), ("animation", "profilAnimation")):
        ligne = next(l for l in source.splitlines() if f"string[] {variable} =" in l)
        assert [c.strip(' "') for c in ligne.split("{", 1)[1].split("}")[0].split(",")] == PROFILES[nom]


def test_adhesions_sans_finances(admin_client, modules_actifs):
    modules_actifs(["presences", "statistiques", "adhesions"])
    for url in ("/caisse", "/impayes", "/tarifs", "/repartition-participation"):
        r = admin_client.get(url, follow_redirects=True)
        assert r.status_code == 200, url
    for url in ("/subventions", "/tresorerie", "/dons"):
        assert admin_client.get(url, follow_redirects=True).status_code == 403, url


def test_finances_sans_adhesions(admin_client, modules_actifs):
    modules_actifs(["presences", "finances"])
    assert admin_client.get("/subventions", follow_redirects=True).status_code == 200
    r = admin_client.get("/caisse", follow_redirects=True)
    assert r.status_code == 403
    assert "Adhésions et caisse" in r.get_data(as_text=True)


def test_adhesion_depuis_la_fiche_suit_le_module():
    from flask import Flask

    from app.services.modules import endpoint_module

    app = Flask(__name__)
    with app.app_context():
        assert endpoint_module("participants.cotisation_creer") == "adhesions"


def test_page_explicative_propose_l_activation(admin_client, modules_actifs):
    modules_actifs(["presences"])
    r = admin_client.get("/salles/", follow_redirects=True)
    assert r.status_code == 403
    page = r.get_data(as_text=True)
    assert "Outil non activé" in page and "Salles et matériel" in page
    assert "/admin/modules" in page


def test_socle_jamais_desactive(admin_client, modules_actifs):
    modules_actifs(["presences", "statistiques"])
    assert admin_client.post("/admin/modules", data={"modules": ["statistiques"]}).status_code == 302
    from app.services.modules import enabled_modules

    assert admin_client.get("/activite/").status_code == 200
    page = admin_client.get("/admin/modules").get_data(as_text=True)
    assert "Animation et accueil" in page            # bouton de profil
    assert 'type="checkbox" checked disabled' in page  # socle non décochable


def test_roles_metier_crees(app):
    with app.app_context():
        from app.models import Role

        animateur = Role.query.filter_by(code="animateur").first()
        accueil = Role.query.filter_by(code="accueil").first()
        assert animateur is not None and accueil is not None
        assert animateur.label == "Animateur / animatrice" and accueil.label == "Accueil"
        perms_anim = {p.code for p in animateur.permissions}
        perms_acc = {p.code for p in accueil.permissions}
        assert "emargement:edit" in perms_anim and "pedagogie:edit" in perms_anim
        # Animateur : pas l'annuaire de toute la structure, pas les montants des financeurs.
        assert "participants:view_all" not in perms_anim and "stats:view" not in perms_anim
        # Accueil : adhésions et caisse, jamais les finances ni l'administration.
        assert {"cotisations:edit", "caisse:view", "inscriptions_annuelles:edit"} <= perms_acc
        assert not any(p.startswith(("subventions", "depenses", "budget", "rh", "admin")) for p in perms_acc)


def _charger_migration():
    chemin = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "e1a7c3f9b2d4_modules_adhesions_socle.py"
    spec = importlib.util.spec_from_file_location("migration_adhesions", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_n_enleve_aucun_ecran():
    """Une structure qui avait « finances » (qui contenait la caisse) garde
    ses écrans d'adhésion ; le socle est ajouté partout ; NULL reste NULL."""
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _charger_migration()
    moteur = sa.create_engine("sqlite://")
    with moteur.begin() as cx:
        cx.execute(sa.text("CREATE TABLE instance_settings (id INTEGER PRIMARY KEY, enabled_modules_json TEXT)"))
        cx.execute(sa.text("INSERT INTO instance_settings VALUES "
                           "(1, '[\"presences\", \"finances\"]'), (2, '[\"statistiques\"]'), "
                           "(3, NULL), (4, 'illisible')"))
        with Operations.context(MigrationContext.configure(cx)):
            migration.upgrade()
        lignes = dict(cx.execute(sa.text("SELECT id, enabled_modules_json FROM instance_settings")).fetchall())
    assert json.loads(lignes[1]) == ["adhesions", "finances", "presences"]
    assert json.loads(lignes[2]) == ["presences", "statistiques"]
    assert lignes[3] is None
    assert lignes[4] == "illisible"


def test_accueil_simplifie_propose_les_adhesions(admin_client, modules_actifs):
    modules_actifs(["presences", "statistiques", "adhesions"])
    with admin_client.session_transaction() as etat:
        etat["ui_mode"] = "simple"
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "Vos outils" in page
    assert 'href="/caisse"' in page and "Adhésions à régler" in page
    assert "Finances et projets" not in page


# --- Kiosque sur téléphone (port HTTP du réseau local) ------------------------------

def test_messages_du_kiosque_affiches_en_http(app, monkeypatch):
    """Cookie de session non sécurisé pour le kiosque servi en HTTP (sinon le
    navigateur le refuse et « Code invalide » ne s'affiche jamais), sécurisé
    partout ailleurs."""
    from app.kiosk import routes as kiosque

    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)
    kiosque._ECHECS_PIN.reinitialiser()
    kiosque._ECHECS_PIN_TOTAL.reinitialiser()
    client = app.test_client()
    r = client.post("/kiosk/", data={"pin": "0000"}, base_url="http://192.168.1.20:8080")
    cookie = r.headers.get("Set-Cookie", "")
    assert "session=" in cookie and "Secure" not in cookie
    page = client.get("/kiosk/", base_url="http://192.168.1.20:8080").get_data(as_text=True)
    assert "Code invalide" in page
    # En HTTPS (administration), le cookie reste sécurisé.
    r = app.test_client().post("/kiosk/", data={"pin": "0000"}, base_url="https://serveur:8443")
    assert "Secure" in r.headers.get("Set-Cookie", "")
    kiosque._ECHECS_PIN.reinitialiser()
    kiosque._ECHECS_PIN_TOTAL.reinitialiser()


def test_page_de_lancement_donne_l_adresse_kiosque(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_BASE_URL", "http://192.168.1.20:8080")
    page = client.get("/launcher/").get_data(as_text=True)
    assert "http://192.168.1.20:8080/kiosk/" in page


def test_pin_plafond_commun_a_tous_les_appareils(app):
    from app.kiosk import routes as kiosque

    kiosque._ECHECS_PIN.reinitialiser()
    kiosque._ECHECS_PIN_TOTAL.reinitialiser()
    try:
        for i in range(60):
            c = app.test_client()
            c.post("/kiosk/", data={"pin": "0000"}, environ_base={"REMOTE_ADDR": f"10.0.{i // 250}.{i % 250 + 1}"})
        r = app.test_client().post("/kiosk/", data={"pin": "1234"}, environ_base={"REMOTE_ADDR": "10.9.9.9"},
                                   follow_redirects=True)
        assert "Trop de codes erronés".encode() in r.data
    finally:
        kiosque._ECHECS_PIN.reinitialiser()
        kiosque._ECHECS_PIN_TOTAL.reinitialiser()


def test_animateur_limite_aux_fiches_de_son_secteur(app):
    from app.extensions import db
    from app.models import Participant, Role, User

    with app.app_context():
        anim = User.query.filter_by(email="anim-secteur@example.org").first()
        if anim is None:
            anim = User(email="anim-secteur@example.org", nom="Animateur", secteur_assigne="Numérique")
            anim.set_password("Anim-secteur-2026")
            anim.roles.append(Role.query.filter_by(code="animateur").one())
            db.session.add(anim)
        autre = Participant(nom="Autresecteur", prenom="Test", created_secteur="Familles",
                            email="autre@example.org")
        sien = Participant(nom="Sonsecteur", prenom="Test", created_secteur="Numérique")
        db.session.add_all([autre, sien])
        db.session.commit()
        autre_id, sien_id = autre.id, sien.id
    c = app.test_client()
    assert c.post("/", data={"email": "anim-secteur@example.org", "password": "Anim-secteur-2026"}).status_code == 302
    try:
        assert c.get(f"/participants/{autre_id}/edit").status_code == 403
        assert c.post(f"/participants/{autre_id}/edit", data={"nom": "Pirate"}).status_code == 403
        assert c.get(f"/participants/{sien_id}/edit").status_code == 200
    finally:
        with app.app_context():
            for pid in (autre_id, sien_id):
                db.session.delete(db.session.get(Participant, pid))
            db.session.commit()
