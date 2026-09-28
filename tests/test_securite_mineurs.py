"""Mineurs sécurité de l'audit restés ouverts (P6)."""
import uuid

import pytest

MDP = "motdepasse-tests"


def _role(app, code, permissions):
    from app.extensions import db
    from app.models import Permission, Role
    with app.app_context():
        role = Role(code=code, label=code)
        for p in permissions:
            perm = Permission.query.filter_by(code=p).first() or Permission(code=p, label=p)
            role.permissions.append(perm)
        db.session.add(role)
        db.session.commit()


def _connexion(app, role_code):
    from app.extensions import db
    from app.models import Role, User
    email = f"{role_code}-{uuid.uuid4().hex[:6]}@test.fr"
    with app.app_context():
        u = User(email=email, nom="Gestion")
        u.set_password(MDP)
        u.roles.append(Role.query.filter_by(code=role_code).one())
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    assert c.post("/", data={"email": email, "password": MDP}).status_code == 302
    return c


def test_admin_users_sans_rbac_ne_cree_pas_plus_puissant_que_lui(app):
    from app.models import User
    cle = uuid.uuid4().hex[:6]
    _role(app, f"comptes{cle}", ["admin:users"])
    _role(app, f"limite{cle}", ["admin:users"])
    client = _connexion(app, f"comptes{cle}")
    for role in ("direction", "accueil"):
        email = f"nouveau-{role}-{cle}@test.fr"
        client.post("/admin/users", data={"email": email, "nom": "X", "password": MDP * 2, "role": role})
        with app.app_context():
            assert User.query.filter_by(email=email).first() is None, role
    email = f"nouveau-ok-{cle}@test.fr"
    client.post("/admin/users", data={"email": email, "nom": "X", "password": MDP * 2, "role": f"limite{cle}"})
    with app.app_context():
        assert User.query.filter_by(email=email).first() is not None
    page = client.get("/admin/users").get_data(as_text=True)
    assert 'value="direction"' not in page


# ---------------------------------------------------------------------------
# Veille des financements : pas de requête vers le réseau interne
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hote, attendu", [
    ("127.0.0.1", False), ("10.1.2.3", False), ("192.168.1.1", False), ("169.254.169.254", False),
    ("100.101.102.103", False), ("localhost", False), ("8.8.8.8", True),
])
def test_hotes_internes_refuses(hote, attendu):
    from app.services.veille_financements import hote_public
    assert hote_public(hote) is attendu


def test_telechargement_vers_le_reseau_interne_refuse(app):
    from app.services import veille_financements as vf
    with app.app_context():
        with pytest.raises(ValueError, match="réseau interne"):
            vf._telecharger("http://127.0.0.1:9/admin")


def test_redirection_revérifiee_et_jeton_retire(monkeypatch):
    import urllib.error
    import urllib.request
    from app.services import veille_financements as vf
    gestionnaire = vf._RedirectionSure()
    requete = urllib.request.Request("https://aides.example/api", headers={"Authorization": "Bearer secret"})
    monkeypatch.setattr(vf, "_url_autorisee", lambda url: "interne" not in url)
    suivie = gestionnaire.redirect_request(requete, None, 302, "Found", {}, "https://ailleurs.example/x")
    assert "Authorization" not in suivie.headers and "authorization" not in {k.lower() for k in suivie.headers}
    meme_site = gestionnaire.redirect_request(requete, None, 302, "Found", {}, "https://aides.example/page2")
    assert meme_site.headers.get("Authorization") == "Bearer secret"
    with pytest.raises(urllib.error.HTTPError):
        gestionnaire.redirect_request(requete, None, 302, "Found", {}, "http://interne/")


# ---------------------------------------------------------------------------
# Cloisonnement : questionnaires et défis transitions
# ---------------------------------------------------------------------------

def _compte_secteur(app, role, secteur):
    from app.extensions import db
    from app.models import Role, User
    email = f"{role}-{uuid.uuid4().hex[:6]}@test.fr"
    with app.app_context():
        u = User(email=email, nom="Equipe", secteur_assigne=secteur)
        u.set_password(MDP)
        u.roles.append(Role.query.filter_by(code=role).one())
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": MDP})
    return c


def test_titres_des_questionnaires_d_un_autre_secteur_masques(app):
    from app.extensions import db
    from app.models import Questionnaire, QuestionnaireSecteur
    cle = uuid.uuid4().hex[:6]
    with app.app_context():
        autre = Questionnaire(nom=f"Secret EPE {cle}", type_questionnaire="autre", is_active=True)
        commun = Questionnaire(nom=f"Commun {cle}", type_questionnaire="autre", is_active=True)
        db.session.add_all([autre, commun])
        db.session.flush()
        db.session.add(QuestionnaireSecteur(questionnaire_id=autre.id, secteur="EPE"))
        db.session.commit()
    page = _compte_secteur(app, "responsable_secteur", "Numérique").get("/questionnaires/").get_data(as_text=True)
    assert f"Commun {cle}" in page and f"Secret EPE {cle}" not in page


def test_defis_transitions_cloisonnes(app):
    import datetime as dt
    from app.extensions import db
    from app.models import DefiTransition, Participant, TransitionThematique
    from app.services.transitions import seed_thematiques, tableau_de_bord
    cle = uuid.uuid4().hex[:6]
    with app.app_context():
        seed_thematiques()
        them = TransitionThematique.query.first()
        epe = Participant(nom=f"Epe{cle}", prenom="T", created_secteur="EPE")
        num = Participant(nom=f"Num{cle}", prenom="T", created_secteur="Numérique")
        db.session.add_all([epe, num])
        db.session.flush()
        for p in (epe, num):
            db.session.add(DefiTransition(titre=f"Défi {p.nom}", thematique_id=them.id, participant_id=p.id,
                                          date_engagement=dt.date(2025, 3, 1)))
        db.session.commit()
        titres = {d.titre for d in tableau_de_bord(2025, "Numérique")["defis"]}
    assert f"Défi Num{cle}" in titres and f"Défi Epe{cle}" not in titres


@pytest.mark.parametrize("valeur, attendu", [
    ("-120,50", "-120,50"), ("-3.5", "-3.5"), ("-1 200,00", "-1 200,00"),
    ("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"), ("-1+cmd|' /C calc'!A0", "'-1+cmd|' /C calc'!A0"),
    ("@SUM(A1)", "'@SUM(A1)"), ("+33612345678", "'+33612345678"),
])
def test_csv_montants_negatifs_restent_des_nombres(valeur, attendu):
    from app.utils.spreadsheet_csv import safe_cell
    assert safe_cell(valeur) == attendu
