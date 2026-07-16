"""Le compte technique (admin_tech) doit pouvoir accéder aux données métier.

Régression : un premier admin créé en admin_tech tombait en 403 sur la liste
des participants (le gabarit ne contenait pas participants:view_all).
"""


def test_admin_tech_a_les_droits_participants(app):
    from app.models import Role

    with app.app_context():
        at = Role.query.filter_by(code="admin_tech").first()
        assert at is not None
        codes = {p.code for p in at.permissions}
        assert "participants:view" in codes
        assert "participants:view_all" in codes


def test_admin_tech_repare_additivement_au_bootstrap(app):
    from app.extensions import db
    from app.models import Role
    from app.rbac import bootstrap_rbac

    with app.app_context():
        at = Role.query.filter_by(code="admin_tech").first()
        # On simule une installation où admin_tech avait perdu l'accès participants.
        at.permissions = [p for p in at.permissions if not p.code.startswith("participants:")]
        db.session.commit()
        assert "participants:view_all" not in {p.code for p in at.permissions}

        bootstrap_rbac()  # doit ré-ajouter sans rien retirer

        at = Role.query.filter_by(code="admin_tech").first()
        assert "participants:view_all" in {p.code for p in at.permissions}
