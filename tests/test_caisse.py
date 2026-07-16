"""Caisse : fond, théorique, comptage avec écart tracé, dépôts, bordereau.

Le théorique se nourrit des règlements espèces/chèque (module cotisations)
et des dons en numéraire — jamais de double saisie côté caisse.
"""
import datetime as dt
import uuid

import pytest


def _login_role(app, email, role_code, secteur=None):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role_code).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": "pw-test-123"})
    return c


def _vider_caisse(app):
    """Repart d'une caisse vierge (les tests partagent la base de session)."""
    from app.extensions import db
    from app.models import CaisseMouvement, Don, Paiement
    with app.app_context():
        CaisseMouvement.query.delete()
        Paiement.query.delete()
        Don.query.delete()
        db.session.commit()


def _encaissement_especes(app, montant, mode="especes"):
    """Crée un règlement réel (participant + cotisation + paiement)."""
    from app.extensions import db
    from app.models import Cotisation, Paiement, Participant
    from app.services.cotisations import annee_scolaire_courante
    with app.app_context():
        suf = uuid.uuid4().hex[:6]
        p = Participant(nom=f"Caisse{suf}", prenom="Test")
        db.session.add(p)
        db.session.flush()
        c = Cotisation(annee_scolaire=annee_scolaire_courante(), type_cotisation="participation",
                       participant_id=p.id, montant_du=montant, date_reference=dt.date.today())
        db.session.add(c)
        db.session.flush()
        db.session.add(Paiement(cotisation_id=c.id, montant=montant,
                                date_paiement=dt.date.today(), mode=mode))
        db.session.commit()


def test_etat_theorique_fond_encaissements_depots(app, admin_client):
    _vider_caisse(app)
    # Fond de caisse 50 €
    admin_client.post("/caisse/fond", data={"montant": "50"})
    # Encaissements réels : 30 € espèces + 40 € chèque
    _encaissement_especes(app, 30.0, mode="especes")
    _encaissement_especes(app, 40.0, mode="cheque")

    with app.app_context():
        from app.services.caisse import etat_caisse
        etat = etat_caisse()
        assert etat["fond"] == 50.0
        assert etat["theorique_especes"] == 80.0     # 50 + 30
        assert etat["recette_deposable"] == 30.0     # au-dessus du fond
        assert etat["cheques_en_attente"] == 40.0


def test_don_en_numeraire_entre_en_caisse(app, admin_client):
    _vider_caisse(app)
    from app.extensions import db
    from app.models import Don
    with app.app_context():
        db.session.add(Don(numero=f"T-{uuid.uuid4().hex[:6]}", annee=2026, donateur_nom="Testeur",
                           montant=25.0, date_don=dt.date.today(),
                           forme_don="numeraire", mode_versement="especes"))
        # Un don annulé et un don en nature ne comptent pas
        db.session.add(Don(numero=f"T-{uuid.uuid4().hex[:6]}", annee=2026, donateur_nom="Annulé",
                           montant=99.0, mode_versement="especes", est_annule=True))
        db.session.add(Don(numero=f"T-{uuid.uuid4().hex[:6]}", annee=2026, donateur_nom="Nature",
                           montant=99.0, forme_don="nature", mode_versement="especes"))
        db.session.commit()
        from app.services.caisse import etat_caisse
        assert etat_caisse()["theorique_especes"] == 25.0


def test_comptage_trace_l_ecart_et_realigne(app, admin_client):
    _vider_caisse(app)
    admin_client.post("/caisse/fond", data={"montant": "50"})
    _encaissement_especes(app, 30.0)
    # On ne compte que 75 € au lieu de 80 € théoriques -> écart -5 €
    r = admin_client.post("/caisse/comptage", data={"montant_constate": "75"}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "écart de -5.00" in body or "écart de -5,00" in body

    with app.app_context():
        from app.models import CaisseMouvement
        from app.services.caisse import etat_caisse
        comptage = CaisseMouvement.query.filter_by(type_mouvement="comptage").one()
        assert comptage.ecart == -5.0
        # L'ajustement automatique réaligne le théorique sur le réel.
        assert CaisseMouvement.query.filter_by(type_mouvement="ajustement").count() == 1
        assert etat_caisse()["theorique_especes"] == 75.0

    # Comptage juste ensuite : aucun nouvel ajustement
    admin_client.post("/caisse/comptage", data={"montant_constate": "75"})
    with app.app_context():
        from app.models import CaisseMouvement
        assert CaisseMouvement.query.filter_by(type_mouvement="ajustement").count() == 1


def test_depot_refuse_au_dela_du_theorique(app, admin_client):
    _vider_caisse(app)
    admin_client.post("/caisse/fond", data={"montant": "50"})
    _encaissement_especes(app, 20.0)
    r = admin_client.post("/caisse/depot", data={"montant_especes": "100"}, follow_redirects=True)
    assert "Impossible de déposer" in r.get_data(as_text=True)
    with app.app_context():
        from app.models import CaisseMouvement
        assert CaisseMouvement.query.filter_by(type_mouvement="depot").count() == 0


def test_depot_especes_et_cheques_avec_bordereau(app, admin_client):
    _vider_caisse(app)
    admin_client.post("/caisse/fond", data={"montant": "50"})
    _encaissement_especes(app, 60.0, mode="especes")
    _encaissement_especes(app, 45.0, mode="cheque")

    r = admin_client.post("/caisse/depot", data={
        "montant_especes": "60", "montant_cheques": "45", "nb_cheques": "3",
        "date_depot": dt.date.today().isoformat(), "commentaire": "remise test",
    })
    assert r.status_code == 302
    assert "/bordereau" in r.headers["Location"]

    body = admin_client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Bordereau de dépôt" in body
    assert "3 chèque(s)" in body
    assert "105.00" in body  # total espèces + chèques

    with app.app_context():
        from app.services.caisse import etat_caisse
        etat = etat_caisse()
        assert etat["theorique_especes"] == 50.0  # il ne reste que le fond
        assert etat["cheques_en_attente"] == 0.0


def test_depot_cheques_exige_leur_nombre(app, admin_client):
    _vider_caisse(app)
    _encaissement_especes(app, 45.0, mode="cheque")
    r = admin_client.post("/caisse/depot", data={"montant_cheques": "45"}, follow_redirects=True)
    assert "nombre de chèques" in r.get_data(as_text=True)


def test_caisse_reservee_aux_bons_droits(app):
    """admin_tech n'a ni caisse:view ni caisse:edit."""
    c = _login_role(app, "caisse-tech@example.org", "admin_tech")
    assert c.get("/caisse").status_code == 403
    assert c.post("/caisse/fond", data={"montant": "50"}).status_code == 403


def test_lecture_seule_voit_sans_modifier(app):
    from app.extensions import db
    from app.models import Permission, Role
    with app.app_context():
        role = Role.query.filter_by(code="caisse_lecture").first()
        if role is None:
            role = Role(code="caisse_lecture", label="Lecture caisse")
            db.session.add(role)
            for code in ("dashboard:view", "caisse:view"):
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    role.permissions.append(perm)
            db.session.commit()
    c = _login_role(app, "caisse-lecture@example.org", "caisse_lecture")
    body = c.get("/caisse").get_data(as_text=True)
    assert "Journal de la caisse" in body
    assert "caisse/comptage" not in body  # pas de formulaire de mutation
    assert c.post("/caisse/comptage", data={"montant_constate": "10"}).status_code == 403
