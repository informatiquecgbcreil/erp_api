"""Pages d'erreur en français, qui disent ce qui s'est passé.

Avant : les gabarits 400/403/404/500 existaient mais n'étaient branchés
nulle part ; on voyait les pages anglaises de Werkzeug (« Forbidden »,
« Not Found »), y compris quand l'application avait un message utile.
"""
import uuid


def _compte(app, role, secteur):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        user = User(email=f"{role}{uuid.uuid4().hex[:8]}@test.fr", nom=role, secteur_assigne=secteur)
        user.set_password("motdepasse-tests")
        user.roles.append(Role.query.filter_by(code=role).one())
        db.session.add(user)
        db.session.commit()
        email = user.email
    client = app.test_client()
    assert client.post("/", data={"email": email, "password": "motdepasse-tests"}).status_code == 302
    return client


def test_page_introuvable_en_francais_avec_l_adresse(admin_client):
    r = admin_client.get("/cette-page-n-existe-pas")
    texte = r.get_data(as_text=True)
    assert r.status_code == 404
    assert "Page introuvable" in texte and "Not Found" not in texte
    assert "/cette-page-n-existe-pas" in texte
    assert "<title>Page introuvable" in texte


def test_droit_manquant_nomme_dans_la_page(app):
    client = _compte(app, "animateur", "Numérique")
    r = client.get("/admin/users")
    texte = r.get_data(as_text=True)
    assert r.status_code == 403
    assert "Accès refusé" in texte and "Forbidden" not in texte
    assert "Gérer les utilisateurs" in texte          # libellé du droit admin:users
    assert "Animateur" in texte                        # rôle de la personne


def test_ecran_de_secteur_sans_secteur_explique(app):
    client = _compte(app, "animateur", None)
    r = client.get("/hart")
    texte = r.get_data(as_text=True)
    assert r.status_code == 403
    assert "aucun secteur" in texte and "Administration" in texte


def test_formulaire_expire_explique(admin_client, app, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    r = admin_client.post("/admin/modules", data={"modules": ["presences"]})
    texte = r.get_data(as_text=True)
    assert r.status_code == 400
    assert "Formulaire expiré" in texte and "CSRF" not in texte


def test_kiosque_garde_sa_presentation(client):
    r = client.get("/kiosk/adresse-inconnue")
    texte = r.get_data(as_text=True)
    assert r.status_code == 404
    assert "Page introuvable" in texte
    assert "Revenir à l'accueil du pointage" in texte or "Revenir à l&#39;accueil du pointage" in texte
    assert "Déconnexion" not in texte


def test_reponse_json_pour_un_appel_json(admin_client):
    r = admin_client.get("/inexistant", headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.get_json()["erreur"] == "Page introuvable"


def test_anonyme_sur_piece_privee_garde_le_401(client):
    r = client.get("/media/justifs/secret.pdf")
    assert r.status_code == 401
    assert "Connexion nécessaire" in r.get_data(as_text=True)


def test_panne_donne_une_reference(app, monkeypatch, caplog):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    with app.test_request_context("/quelque-part"):
        reponse = app.make_response(app.handle_exception(RuntimeError("panne simulée")))
    texte = reponse.get_data(as_text=True)
    assert reponse.status_code == 500
    assert "Une erreur est survenue" in texte
    assert "Référence à donner à l&#39;administrateur" in texte or "Référence à donner à l'administrateur" in texte
    reference = texte.split("administrateur : ")[1].split("<")[0].strip()
    assert reference in caplog.text
