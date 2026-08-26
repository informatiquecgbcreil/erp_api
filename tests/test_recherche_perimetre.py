"""Périmètre de la recherche globale.

Cas réel : une responsable de secteur voyait tous les participants dans la
liste mais sa barre de recherche ne renvoyait rien — la liste et la
recherche n'appliquaient pas la même règle de périmètre.
"""
import datetime as dt
import uuid


def _client(app, *, email, secteur, role="responsable_secteur"):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    assert c.post("/", data={"email": email, "password": "pw-test-123"}).status_code == 302
    return c


def _participant(app, *, nom, created_secteur):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        p = Participant(nom=nom, prenom="Dorgie", created_secteur=created_secteur,
                        ville="Nogent sur Oise", genre="Homme",
                        date_naissance=dt.date(1985, 3, 30))
        db.session.add(p)
        db.session.commit()
        return p.id


def _chercher(client, terme):
    r = client.get(f"/api/global-search?q={terme}")
    assert r.status_code == 200, r.status_code
    return r.get_json()


def test_responsable_secteur_trouve_les_participants_d_un_autre_secteur(app):
    """Régression : elle les voit dans la liste, elle doit les trouver.

    Le rôle responsable_secteur porte participants:view_all (exception
    assumée dans le RBAC) : la recherche ne doit pas être plus stricte.
    """
    tag = uuid.uuid4().hex[:6]
    nom = f"DAWA{tag}"
    _participant(app, nom=nom, created_secteur=f"Autre{tag}")
    client = _client(app, email=f"insertion-{tag}@ex.org",
                     secteur="Insertion Sociale et Professionnelle")

    # La liste des participants la montre déjà…
    liste = client.get("/participants/", follow_redirects=True).get_data(as_text=True)
    assert nom in liste

    # … la recherche doit la trouver aussi.
    donnees = _chercher(client, nom)
    labels = " ".join(str(r.get("label", "")) for r in donnees.get("results", []))
    assert nom in labels, f"participant introuvable : {donnees.get('results')}"


def test_role_sans_view_all_reste_cloisonne(app):
    """Non-régression du cloisonnement : sans participants:view_all, un rôle
    ne doit pas se mettre à voir les participants des autres secteurs.

    Aucun rôle du référentiel standard n'est dans ce cas (responsable_secteur
    porte view_all par choix assumé) : on en fabrique donc un pour éprouver
    vraiment la garantie plutôt que de la supposer.
    """
    from app.extensions import db
    from app.models import Permission, Role, User

    tag = uuid.uuid4().hex[:6]
    nom = f"SECRET{tag}"
    _participant(app, nom=nom, created_secteur=f"Autre{tag}")

    code_role = f"limite{tag}"
    email = f"limite-{tag}@ex.org"
    with app.app_context():
        role = Role(code=code_role, label="Rôle cloisonné (test)")
        db.session.add(role)   # avant d'attacher : sinon rien n'est enregistré
        db.session.flush()
        for code_perm in ("dashboard:view", "participants:view"):
            perm = Permission.query.filter_by(code=code_perm).first()
            assert perm is not None, f"permission {code_perm} absente du référentiel"
            role.permissions.append(perm)
        u = User(email=email, nom="Limite", secteur_assigne=f"Mien{tag}")
        u.set_password("pw-test-123")
        u.roles.append(role)
        db.session.add(u)
        db.session.commit()
        # Le rôle voit bien les participants, mais pas ceux des autres secteurs.
        assert u.has_perm("participants:view"), "le rôle de test doit pouvoir voir les participants"
        assert not u.has_perm("participants:view_all")
        assert not u.has_perm("scope:all_secteurs")

    client = app.test_client()
    assert client.post("/", data={"email": email, "password": "pw-test-123"}).status_code == 302

    donnees = _chercher(client, nom)
    labels = " ".join(str(r.get("label", "")) for r in donnees.get("results", []))
    assert nom not in labels, "un rôle cloisonné ne doit pas voir ce participant"


def test_filtre_secteur_explicite_toujours_honore(app):
    """« secteur:X » tapé dans la recherche reste un filtre volontaire."""
    tag = uuid.uuid4().hex[:6]
    nom = f"CIBLE{tag}"
    _participant(app, nom=nom, created_secteur=f"Autre{tag}")
    client = _client(app, email=f"filtre-{tag}@ex.org",
                     secteur="Insertion Sociale et Professionnelle")

    donnees = _chercher(client, f"{nom}%20secteur:Insertion%20Sociale%20et%20Professionnelle")
    labels = " ".join(str(r.get("label", "")) for r in donnees.get("results", []))
    assert nom not in labels, "le filtre secteur explicite doit exclure ce participant"
