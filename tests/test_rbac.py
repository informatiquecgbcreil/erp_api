"""Tests RBAC : permissions, cloisonnement par secteur, migration legacy.

Le système de droits repose sur les rôles/permissions RBAC en base.
L'ancienne colonne texte ``user.role`` (legacy) n'est conservée que pour
la compatibilité de schéma : elle est migrée vers un rôle RBAC au
démarrage par ``bootstrap_rbac`` et ne donne aucun droit par elle-même.
"""
import pytest

SECTEUR_EMAIL = "familles@example.org"
SECTEUR_PASSWORD = "motdepasse-tests"


@pytest.fixture(scope="module")
def donnees_rbac(app):
    """Un responsable de secteur (Familles) + une subvention par secteur."""
    with app.app_context():
        from app.extensions import db
        from app.models import Role, Subvention, User

        user = User.query.filter_by(email=SECTEUR_EMAIL).first()
        if user is None:
            user = User(email=SECTEUR_EMAIL, nom="Responsable Familles")
            user.set_password(SECTEUR_PASSWORD)
            user.secteur_assigne = "Familles"
            user.roles.append(Role.query.filter_by(code="responsable_secteur").first())
            db.session.add(user)

        sub_familles = Subvention.query.filter_by(nom="Sub test Familles").first()
        if sub_familles is None:
            sub_familles = Subvention(nom="Sub test Familles", secteur="Familles", annee_exercice=2026)
            db.session.add(sub_familles)
        sub_numerique = Subvention.query.filter_by(nom="Sub test Numérique").first()
        if sub_numerique is None:
            sub_numerique = Subvention(nom="Sub test Numérique", secteur="Numérique", annee_exercice=2026)
            db.session.add(sub_numerique)

        db.session.commit()
        return {"sub_familles_id": sub_familles.id, "sub_numerique_id": sub_numerique.id}


@pytest.fixture()
def secteur_client(app, donnees_rbac):
    """Client HTTP connecté en responsable de secteur (Familles)."""
    c = app.test_client()
    r = c.post("/", data={"email": SECTEUR_EMAIL, "password": SECTEUR_PASSWORD})
    assert r.status_code == 302, "la connexion du responsable secteur a échoué"
    return c


def test_responsable_secteur_acces_pages_metier(secteur_client):
    assert secteur_client.get("/dashboard").status_code == 200
    assert secteur_client.get("/subventions").status_code == 200


@pytest.mark.parametrize("url", ["/admin/users", "/rbac-test"])
def test_responsable_secteur_refuse_pages_admin(secteur_client, url):
    r = secteur_client.get(url)
    assert r.status_code == 403, f"GET {url} -> {r.status_code} (403 attendu)"


def test_cloisonnement_secteur_sur_subvention(secteur_client, donnees_rbac):
    # Sa propre subvention : accessible
    r = secteur_client.get(f"/subvention/{donnees_rbac['sub_familles_id']}/pilotage")
    assert r.status_code == 200

    # Celle d'un autre secteur : refusée
    r = secteur_client.get(f"/subvention/{donnees_rbac['sub_numerique_id']}/pilotage")
    assert r.status_code == 403


def test_has_role_via_rbac_et_alias(app):
    with app.app_context():
        from tests.conftest import ADMIN_EMAIL
        from app.models import User

        admin = User.query.filter_by(email=ADMIN_EMAIL).first()
        assert admin.has_role("direction")
        assert admin.has_role("directrice"), "l'alias directrice -> direction doit fonctionner"
        assert not admin.has_role("admin_tech")
        assert not admin.has_role(None)


def test_colonne_legacy_seule_ne_donne_aucun_droit(app):
    """Cible de la migration : sans rôle RBAC, la vieille colonne texte
    ``user.role`` ne doit conférer ni rôle ni permission."""
    with app.app_context():
        from app.extensions import db
        from app.models import User

        user = User(email="legacy@example.org", nom="Legacy Seul", role="direction")
        user.set_password("motdepasse-tests")
        db.session.add(user)
        db.session.commit()
        try:
            assert user.roles == []
            assert not user.has_perm("admin:rbac")
            assert not user.has_role("direction"), (
                "le RBAC est la seule source de vérité : la colonne legacy ne doit plus donner de rôle"
            )
        finally:
            db.session.delete(user)
            db.session.commit()


def test_rattrapage_legacy_au_demarrage(app):
    """Filet de migration : au démarrage, un utilisateur sans rôle RBAC
    reçoit le rôle correspondant à sa colonne legacy."""
    with app.app_context():
        from app.extensions import db
        from app.models import User
        from app.rbac import bootstrap_rbac

        user = User(email="ancien@example.org", nom="Ancien Compte", role="direction")
        user.set_password("motdepasse-tests")
        db.session.add(user)
        db.session.commit()
        try:
            assert user.roles == []
            bootstrap_rbac()
            assert [r.code for r in user.roles] == ["direction"]
            assert user.has_perm("admin:rbac")
        finally:
            db.session.delete(user)
            db.session.commit()
