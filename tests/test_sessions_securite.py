"""Sessions de l'équipe (mineur sécurité de l'audit)."""
import time

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def _connecte(app):
    c = app.test_client()
    assert c.post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 302
    return c


def test_cookie_copie_avant_la_deconnexion_n_ouvre_plus_rien(app):
    client = _connecte(app)
    assert client.get("/dashboard").status_code == 200
    cookie = client.get_cookie("session") or client.get_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
    assert cookie is not None
    copie = cookie.value
    client.post("/logout")
    pirate = app.test_client()
    pirate.set_cookie(cookie.key, copie)
    r = pirate.get("/dashboard")
    assert r.status_code == 302 and "/dashboard" not in r.headers["Location"]


def test_fin_de_session_apres_inactivite(app):
    from app.services.sessions_securite import CLE_VU
    client = _connecte(app)
    with client.session_transaction() as s:
        s[CLE_VU] = int(time.time()) - (app.config["SESSION_INACTIVITE_MINUTES"] * 60 + 5)
    r = client.get("/dashboard", follow_redirects=True)
    assert "inactivité" in r.get_data(as_text=True)


def test_duree_maximale_de_session(app):
    from app.services.sessions_securite import CLE_DEBUT
    client = _connecte(app)
    with client.session_transaction() as s:
        s[CLE_DEBUT] = int(time.time()) - (app.config["SESSION_DUREE_MAX_HEURES"] * 3600 + 5)
    r = client.get("/dashboard", follow_redirects=True)
    assert "durée maximale" in r.get_data(as_text=True)


def test_activite_reguliere_garde_la_session(app):
    client = _connecte(app)
    for _ in range(3):
        assert client.get("/dashboard").status_code == 200


# ---------------------------------------------------------------------------
# Connexion : durée constante, plafond par adresse, réinitialisation bornée
# ---------------------------------------------------------------------------

def _compte(app, email, mot_de_passe, methode=None):
    from werkzeug.security import generate_password_hash
    from app.extensions import db
    from app.models import User
    with app.app_context():
        u = User(email=email, nom="Ancien")
        u.password_hash = generate_password_hash(mot_de_passe, method=methode) if methode else generate_password_hash(mot_de_passe)
        db.session.add(u)
        db.session.commit()
        return u.id


def test_hachage_pbkdf2_renouvele_a_la_connexion(app):
    import uuid
    from app.extensions import db
    from app.models import User
    email = f"ancien-{uuid.uuid4().hex[:6]}@test.fr"
    uid = _compte(app, email, "motdepasse-ancien", methode="pbkdf2:sha256")
    app.test_client().post("/", data={"email": email, "password": "motdepasse-ancien"})
    with app.app_context():
        assert not db.session.get(User, uid).password_hash.startswith("pbkdf2:")


def test_chaque_tentative_verifie_un_hachage_de_chaque_sorte(app, monkeypatch):
    import uuid
    from app.auth import routes
    from app.models import User
    appels = []
    vrai = routes.check_password_hash
    monkeypatch.setattr(routes, "check_password_hash", lambda h, p: appels.append(h.split(":")[0]) or vrai(h, p))
    email = f"ancien-{uuid.uuid4().hex[:6]}@test.fr"
    _compte(app, email, "motdepasse-ancien", methode="pbkdf2:sha256")
    with app.app_context():
        routes._verifier_a_duree_constante(None, "x")
        inconnu = sorted(appels)
        appels.clear()
        routes._verifier_a_duree_constante(User.query.filter_by(email=email).one(), "x")
        ancien = sorted(appels + ["pbkdf2"])  # le vrai contrôle passe par User.check_password
    assert inconnu == ["pbkdf2", "scrypt"] and ancien == ["pbkdf2", "scrypt"]


def test_plafond_par_adresse_tous_comptes_confondus(app):
    from app.services import connexion_securite as cs
    adresse = "198.51.100.77"
    for i in range(cs.MAX_ECHECS_ADRESSE):
        app.test_client().post("/", data={"email": f"inconnu{i}@test.fr", "password": "x"},
                               environ_base={"REMOTE_ADDR": adresse})
    bloque = app.test_client().post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                                    environ_base={"REMOTE_ADDR": adresse})
    assert bloque.status_code == 200 and "Trop de tentatives" in bloque.get_data(as_text=True)
    ailleurs = app.test_client().post("/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                                      environ_base={"REMOTE_ADDR": "198.51.100.78"})
    assert ailleurs.status_code == 302


def test_reinitialisation_bornee(app, monkeypatch):
    from app.auth import routes
    envois = []
    monkeypatch.setattr(routes, "_send_password_reset_email", lambda email, lien: envois.append(email) or True)
    monkeypatch.setattr(routes, "_DEMANDES_REINIT_ADRESSE", None)
    monkeypatch.setattr(routes, "_DEMANDES_REINIT_EMAIL", None)
    for _ in range(6):
        r = app.test_client().post("/password-reset", data={"email": ADMIN_EMAIL},
                                   environ_base={"REMOTE_ADDR": "198.51.100.90"})
        assert "Si un compte correspond" in r.get_data(as_text=True)
    assert len(envois) == 3
