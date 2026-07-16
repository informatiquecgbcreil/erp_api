"""Lot 1 — Sécurité & comptes : garde-fou « dernier administrateur »,
édition d'un utilisateur, réinitialisation de mot de passe, désactivation."""
import uuid

ADMIN_EMAIL = "admin@example.org"
ADMIN_PASSWORD = "motdepasse-tests"


def _admin_direction(db):
    from app.models import User
    return User.query.filter_by(email=ADMIN_EMAIL).first()


# --- Garde-fou « dernier administrateur » ---------------------------------
def test_helper_compte_admins_actifs(app):
    from app.admin.routes import _nb_admins_actifs, _est_admin

    with app.app_context():
        from app.extensions import db
        admin = _admin_direction(db)
        assert _est_admin(admin) is True
        # En excluant le seul admin (direction), il n'en reste aucun.
        assert _nb_admins_actifs(exclure_user_id=admin.id) == 0


def test_suppression_role_direction_bloquee(admin_client, app):
    """Supprimer le rôle qui donne l'accès admin au dernier admin est refusé."""
    from app.models import Role

    r = admin_client.post("/admin/delete_role", data={"role_code": "direction"}, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Role.query.filter_by(code="direction").first() is not None  # toujours là


def test_retrait_admin_rbac_du_role_direction_bloque(admin_client, app):
    from app.models import Role

    # On tente de sauvegarder les perms de "direction" SANS admin:rbac.
    admin_client.post("/admin/save_role_perms",
                      data={"role_code": "direction", "perm_codes": ["dashboard:view"]},
                      follow_redirects=True)
    with app.app_context():
        direction = Role.query.filter_by(code="direction").first()
        codes = {p.code for p in direction.permissions}
        assert "admin:rbac" in codes  # garde-fou : non retiré


def test_demotion_dernier_admin_bloquee(admin_client, app):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        admin = _admin_direction(db)
        aid = admin.id

    admin_client.post("/admin/set_user_roles",
                      data={"user_id": aid, "role": "responsable_secteur"},
                      follow_redirects=True)
    with app.app_context():
        admin = db.session.get(User, aid)
        assert "direction" in (admin.role_codes or [])  # rôle conservé


# --- Édition d'un utilisateur ---------------------------------------------
def test_edit_user_nom_secteur_mdp(admin_client, app):
    from app.extensions import db
    from app.models import User, Role

    email = f"u{uuid.uuid4().hex[:8]}@ex.org"
    with app.app_context():
        u = User(email=email, nom="Avant")
        u.set_password("ancienmotdepasse")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()
        uid = u.id

    r = admin_client.post(f"/admin/users/{uid}/edit",
                          data={"nom": "Après", "secteur_assigne": "Numérique",
                                "role": "responsable_secteur", "password": "nouveaumotdepasse"},
                          follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        u = db.session.get(User, uid)
        assert u.nom == "Après"
        assert u.secteur_assigne == "Numérique"
        assert u.check_password("nouveaumotdepasse")

    # Le nouveau mot de passe permet de se connecter.
    c = app.test_client()
    login = c.post("/", data={"email": email, "password": "nouveaumotdepasse"})
    assert login.status_code == 302


# --- Désactivation -> blocage de connexion --------------------------------
def test_desactivation_bloque_connexion(admin_client, app):
    from app.extensions import db
    from app.models import User, Role

    email = f"d{uuid.uuid4().hex[:8]}@ex.org"
    with app.app_context():
        u = User(email=email, nom="Désactivable")
        u.set_password("motdepasse-actif")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()
        uid = u.id

    # connexion OK avant désactivation
    assert app.test_client().post("/", data={"email": email, "password": "motdepasse-actif"}).status_code == 302

    admin_client.post(f"/admin/users/{uid}/toggle-actif", follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, uid).actif is False

    # connexion refusée après désactivation
    r = app.test_client().post("/", data={"email": email, "password": "motdepasse-actif"})
    assert r.status_code == 200
    assert "désactivé" in r.get_data(as_text=True)

    # réactivation
    admin_client.post(f"/admin/users/{uid}/toggle-actif", follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, uid).actif is True


def test_suppression_user_non_admin_ok(admin_client, app):
    from app.extensions import db
    from app.models import User, Role

    email = f"s{uuid.uuid4().hex[:8]}@ex.org"
    with app.app_context():
        u = User(email=email, nom="ASupprimer")
        u.set_password("motdepasse-suppr")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()
        uid = u.id

    admin_client.post(f"/admin/delete/{uid}", follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, uid) is None
