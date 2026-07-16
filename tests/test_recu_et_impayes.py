"""Reçu de règlement (fiche) + vue globale des impayés."""
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


def _cotisation(app, *, montant_du, regle=0.0, type_cotisation="participation",
                secteur=None, annee=None):
    """Crée un participant + une cotisation + éventuellement un règlement."""
    from app.extensions import db
    from app.models import Cotisation, Paiement, Participant
    from app.services.cotisations import annee_scolaire_courante
    annee = annee or annee_scolaire_courante()
    with app.app_context():
        suf = uuid.uuid4().hex[:6]
        p = Participant(nom=f"Imp{suf}", prenom="T", created_secteur=secteur)
        db.session.add(p)
        db.session.flush()
        c = Cotisation(annee_scolaire=annee, type_cotisation=type_cotisation,
                       participant_id=p.id, montant_du=montant_du, date_reference=dt.date.today())
        db.session.add(c)
        db.session.flush()
        if regle > 0:
            db.session.add(Paiement(cotisation_id=c.id, montant=regle,
                                    date_paiement=dt.date.today(), mode="especes"))
        db.session.commit()
        return {"pid": p.id, "cid": c.id, "suf": suf, "annee": annee}


# ---------- Reçu de règlement ----------

def test_recu_de_reglement(app, admin_client):
    d = _cotisation(app, montant_du=30.0, regle=10.0)
    r = admin_client.get(f"/participants/{d['pid']}/cotisation/{d['cid']}/recu")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "REÇU DE RÈGLEMENT" in body
    assert "10.00" in body          # total réglé
    assert "Règlement partiel" in body
    assert f"Imp{d['suf']}" in body


def test_lien_recu_visible_seulement_apres_un_reglement(app, admin_client):
    # Sans règlement : pas de lien reçu sur la fiche
    d0 = _cotisation(app, montant_du=30.0, regle=0.0)
    body = admin_client.get(f"/participants/{d0['pid']}/synthese").get_data(as_text=True)
    assert "cotisation/{}/recu".format(d0["cid"]) not in body
    # Avec règlement : lien présent
    d1 = _cotisation(app, montant_du=30.0, regle=15.0)
    body = admin_client.get(f"/participants/{d1['pid']}/synthese").get_data(as_text=True)
    assert "Imprimer un reçu" in body


def test_recu_refuse_sans_cotisations_view(app):
    d = _cotisation(app, montant_du=30.0, regle=10.0)
    c = _login_role(app, "recu-tech@example.org", "admin_tech")  # pas de cotisations:view
    r = c.get(f"/participants/{d['pid']}/cotisation/{d['cid']}/recu")
    assert r.status_code == 403


# ---------- Impayés globaux ----------

def test_impayes_liste_triee_et_total(app, admin_client):
    from app.services.cotisations import annee_scolaire_courante
    annee = annee_scolaire_courante()
    # Trois cotisations : reste 30, reste 5, soldée (exclue)
    a = _cotisation(app, montant_du=30.0, regle=0.0, annee=annee)
    b = _cotisation(app, montant_du=20.0, regle=15.0, annee=annee)   # reste 5
    solde = _cotisation(app, montant_du=10.0, regle=10.0, annee=annee)

    body = admin_client.get(f"/impayes?annee={annee}").get_data(as_text=True)
    assert f"Imp{a['suf']}" in body
    assert f"Imp{b['suf']}" in body
    assert f"Imp{solde['suf']}" not in body   # soldée exclue

    # Le plus gros reste (30) apparaît avant le petit (5) dans le HTML.
    assert body.index(f"Imp{a['suf']}") < body.index(f"Imp{b['suf']}")


def test_impayes_borne_par_secteur_pour_role_borne(app):
    from app.extensions import db
    from app.models import Permission, Role
    from app.services.cotisations import annee_scolaire_courante
    annee = annee_scolaire_courante()
    suf = uuid.uuid4().hex[:6]
    mien, autre = f"Sect{suf}", f"Autre{suf}"
    dedans = _cotisation(app, montant_du=40.0, regle=0.0, secteur=mien, annee=annee)
    dehors = _cotisation(app, montant_du=40.0, regle=0.0, secteur=autre, annee=annee)

    # Rôle borné : cotisations:view SANS participants:view_all ni scope:all_secteurs.
    with app.app_context():
        role = Role.query.filter_by(code="impaye_borne").first()
        if role is None:
            role = Role(code="impaye_borne", label="Impayés borné")
            db.session.add(role)
            for code in ("dashboard:view", "cotisations:view"):
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    role.permissions.append(perm)
            db.session.commit()

    c = _login_role(app, f"impaye-borne-{suf}@example.org", "impaye_borne", secteur=mien)
    body = c.get(f"/impayes?annee={annee}").get_data(as_text=True)
    assert f"Imp{dedans['suf']}" in body
    assert f"Imp{dehors['suf']}" not in body


def test_impayes_refuse_sans_cotisations_view(app):
    c = _login_role(app, "impaye-tech@example.org", "admin_tech")
    assert c.get("/impayes").status_code == 403
