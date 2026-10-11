"""Accueil sans secteur : la rentrée lui était fermée.

Le rôle Accueil traite les bulletins de rentrée de tout le monde, sans
recevoir la portée structure (qui ouvrirait aussi le pilotage financier).
Sans secteur sur son compte, les inscriptions annuelles répondaient 403 :
permission dédiée « inscriptions_annuelles:tous_secteurs », attribuée aussi
aux bases existantes au démarrage.
"""
import uuid
from datetime import date

ANNEE = 2031


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


def _bulletin(app, secteur):
    from app.extensions import db
    from app.models import InscriptionAnnuelle
    nom = "Rentree" + uuid.uuid4().hex[:8]
    with app.app_context():
        i = InscriptionAnnuelle(annee_scolaire=ANNEE, date_inscription=date(ANNEE, 9, 2),
                                nom=nom, prenom="Lina", statut="en_attente", created_secteur=secteur)
        db.session.add(i)
        db.session.commit()
        return nom, i.id


def test_accueil_sans_secteur_ouvre_la_rentree(app):
    client = _compte(app, "accueil", None)
    assert client.get("/inscriptions-annuelles/").status_code == 200
    assert client.get("/inscriptions-annuelles/nouvelle").status_code == 200
    assert client.get("/inscriptions-annuelles/a-regulariser").status_code == 200


def test_accueil_voit_les_bulletins_de_tous_les_secteurs(app):
    nom_epe, id_epe = _bulletin(app, "EPE")
    nom_sans, _ = _bulletin(app, None)
    client = _compte(app, "accueil", "Numérique")
    page = client.get(f"/inscriptions-annuelles/?annee={ANNEE}").get_data(as_text=True)
    assert nom_epe in page and nom_sans in page
    assert client.get(f"/inscriptions-annuelles/{id_epe}").status_code == 200


def test_secteur_borne_ne_voit_pas_les_autres(app):
    """Le cloisonnement des autres rôles ne bouge pas."""
    nom_epe, id_epe = _bulletin(app, "EPE")
    client = _compte(app, "responsable_secteur", "Numérique")
    page = client.get(f"/inscriptions-annuelles/?annee={ANNEE}").get_data(as_text=True)
    assert nom_epe not in page
    assert client.get(f"/inscriptions-annuelles/{id_epe}").status_code in (403, 404)


def test_permission_attribuee_aux_bases_existantes(app):
    """Base créée avant la permission : l'accueil la reçoit au démarrage."""
    from app.extensions import db
    from app.models import Permission, Role
    from app.rbac import PERMS_AUTO_GRANT, bootstrap_rbac
    assert PERMS_AUTO_GRANT["inscriptions_annuelles:tous_secteurs"] == ("accueil",)
    with app.app_context():
        role = Role.query.filter_by(code="accueil").one()
        perm = Permission.query.filter_by(code="inscriptions_annuelles:tous_secteurs").one()
        role.permissions = [p for p in role.permissions if p.code != perm.code]
        db.session.delete(perm)
        db.session.commit()

        bootstrap_rbac()

        role = Role.query.filter_by(code="accueil").one()
        codes = {p.code for p in role.permissions}
        assert "inscriptions_annuelles:tous_secteurs" in codes


def test_migration_ouvre_toute_la_structure_a_l_accueil_existant(app):
    """Rôle Accueil créé avec l'ancien gabarit : la migration lui accorde la
    portée structure, la caisse, les stats, questionnaires et partenaires."""
    import flask_migrate
    from app.extensions import db
    from app.models import Role
    ajouts = {"scope:all_secteurs", "caisse:edit", "stats:view_all", "statsimpact:view_all",
              "bilans:view", "questionnaires:edit", "partenaires:edit"}
    with app.app_context():
        role = Role.query.filter_by(code="accueil").one()
        role.permissions = [p for p in role.permissions if p.code not in ajouts]
        db.session.commit()

        flask_migrate.downgrade(revision="d5b7c9e1f3a4")
        flask_migrate.upgrade()

        db.session.expire_all()
        codes = {p.code for p in Role.query.filter_by(code="accueil").one().permissions}
        assert ajouts <= codes
        assert "rh:view" not in codes and "admin:users" not in codes and "depenses:view" not in codes
