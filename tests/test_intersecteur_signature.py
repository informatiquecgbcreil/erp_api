"""Ateliers intersecteur + signature de présence à distance.

Intersecteur :
- un animateur borné à son secteur crée un atelier imputé à un AUTRE secteur ;
- l'atelier est visible et utilisable (émargement, grille) par tous les
  secteurs ; ses séances portent le secteur d'imputation (stats) ;
- un atelier ordinaire d'un autre secteur reste invisible.

Signature à distance :
- liens personnels générés pour les présences non signées uniquement ;
- la page publique /kiosk/signer/<jeton> fonctionne sans connexion (et via
  la façade tunnel) ; la signature enregistre le fichier et tue le jeton
  (usage unique).
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


# ---------- Ateliers intersecteur ----------

def test_creation_intersecteur_impute_un_autre_secteur(app):
    """Le secteur d'imputation doit être un secteur DÉCLARÉ de la structure
    (référentiel Secteur) : on en crée un vrai pour le test."""
    from app.extensions import db
    from app.models import AtelierActivite, Secteur
    suf = uuid.uuid4().hex[:6]
    mien, cible = f"Num{suf}", f"AnimGlob{suf}"
    with app.app_context():
        db.session.add(Secteur(code=cible.lower(), label=cible, is_active=True))
        db.session.commit()
    c = _login_role(app, f"inter-{suf}@ex.org", "responsable_secteur", secteur=mien)

    r = c.post("/activite/atelier/new", data={
        "nom": f"Repair café{suf}", "type_atelier": "COLLECTIF",
        "est_intersecteur": "1", "secteur_stats": cible, "is_active": "1",
    })
    assert r.status_code == 302
    with app.app_context():
        a = AtelierActivite.query.filter_by(nom=f"Repair café{suf}").one()
        assert a.est_intersecteur is True
        assert a.secteur == cible          # stats imputées au secteur choisi

    # Un secteur d'imputation INCONNU est refusé : l'atelier retombe sur le
    # secteur du créateur (pas d'invention silencieuse de secteur).
    r = c.post("/activite/atelier/new", data={
        "nom": f"Inconnu{suf}", "type_atelier": "COLLECTIF",
        "est_intersecteur": "1", "secteur_stats": f"Bidon{suf}", "is_active": "1",
    })
    with app.app_context():
        a = AtelierActivite.query.filter_by(nom=f"Inconnu{suf}").one()
        assert a.secteur == mien


def test_intersecteur_visible_et_utilisable_par_les_autres_secteurs(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    suf = uuid.uuid4().hex[:6]
    autre_secteur = f"Fam{suf}"
    with app.app_context():
        inter = AtelierActivite(nom=f"FeteInter{suf}", secteur=f"AG{suf}",
                                type_atelier="COLLECTIF", est_intersecteur=True)
        prive = AtelierActivite(nom=f"Prive{suf}", secteur=f"AG{suf}", type_atelier="COLLECTIF")
        db.session.add_all([inter, prive]); db.session.flush()
        s = SessionActivite(atelier_id=inter.id, secteur=inter.secteur, session_type="COLLECTIF",
                            date_session=dt.date.today())
        db.session.add(s); db.session.commit()
        inter_id, sid = inter.id, s.id

    c = _login_role(app, f"autre-{suf}@ex.org", "responsable_secteur", secteur=autre_secteur)
    # Liste : l'intersecteur oui, l'atelier privé d'un autre secteur non.
    body = c.get("/activite/").get_data(as_text=True)
    assert f"FeteInter{suf}" in body
    assert "intersecteur" in body
    assert f"Prive{suf}" not in body
    # Séances + émargement accessibles.
    assert c.get(f"/activite/atelier/{inter_id}/sessions").status_code == 200
    assert c.get(f"/activite/session/{sid}/emargement").status_code == 200
    # Grille : l'atelier intersecteur y apparaît.
    body = c.get("/activite/saisie-grille").get_data(as_text=True)
    assert f"FeteInter{suf}" in body


def test_seance_creee_sur_intersecteur_porte_le_secteur_dimputation(app):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    suf = uuid.uuid4().hex[:6]
    imputation = f"AG{suf}"
    with app.app_context():
        inter = AtelierActivite(nom=f"GrilleInter{suf}", secteur=imputation,
                                type_atelier="COLLECTIF", est_intersecteur=True)
        db.session.add(inter); db.session.commit()
        inter_id = inter.id

    c = _login_role(app, f"imput-{suf}@ex.org", "responsable_secteur", secteur=f"Num{suf}")
    mois = dt.date.today().strftime("%Y-%m")
    r = c.post(f"/activite/saisie-grille?atelier_id={inter_id}&mois={mois}",
               data={"action": "add_session", "atelier_id": inter_id, "mois": mois,
                     "date_session": dt.date.today().isoformat()})
    assert r.status_code == 302
    with app.app_context():
        s = SessionActivite.query.filter_by(atelier_id=inter_id).one()
        assert s.secteur == imputation      # les stats iront au secteur assigné


# ---------- Signature à distance ----------

def _seance_avec_presences(app, *, suf, nb=2):
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    with app.app_context():
        sect = f"Sig{suf}"
        at = AtelierActivite(nom=f"Velo{suf}", secteur=sect, type_atelier="COLLECTIF")
        db.session.add(at); db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=sect, session_type="COLLECTIF",
                            date_session=dt.date.today(), heure_debut="14:00", heure_fin="16:00")
        db.session.add(s); db.session.flush()
        pids = []
        for i in range(nb):
            p = Participant(nom=f"Sig{suf}{i}", prenom=f"P{i}")
            db.session.add(p); db.session.flush()
            db.session.add(PresenceActivite(session_id=s.id, participant_id=p.id))
            pids.append(p.id)
        db.session.commit()
        return {"sid": s.id, "secteur": sect, "pids": pids}


def test_generation_des_liens_pour_presences_non_signees(app, admin_client):
    from app.extensions import db
    from app.models import PresenceActivite
    suf = uuid.uuid4().hex[:6]
    m = _seance_avec_presences(app, suf=suf, nb=3)
    with app.app_context():
        # Une présence déjà signée : pas de lien pour elle.
        pr = PresenceActivite.query.filter_by(session_id=m["sid"]).first()
        pr.signature_path = "signatures/deja.png"
        db.session.commit()
        deja_signee_id = pr.id

    r = admin_client.post(f"/activite/session/{m['sid']}/signature-liens")
    assert r.status_code == 302
    with app.app_context():
        rows = PresenceActivite.query.filter_by(session_id=m["sid"]).all()
        avec_token = [p for p in rows if p.signature_token]
        assert len(avec_token) == 2
        assert all(p.id != deja_signee_id for p in avec_token)

    # La page émargement affiche les liens personnels.
    body = admin_client.get(f"/activite/session/{m['sid']}/emargement").get_data(as_text=True)
    assert "Signatures à distance" in body
    assert "/kiosk/signer/" in body


def test_signature_publique_usage_unique(app, client, admin_client):
    from app.extensions import db
    from app.models import PresenceActivite
    suf = uuid.uuid4().hex[:6]
    m = _seance_avec_presences(app, suf=suf, nb=1)
    admin_client.post(f"/activite/session/{m['sid']}/signature-liens")
    with app.app_context():
        pr = PresenceActivite.query.filter_by(session_id=m["sid"]).one()
        token = pr.signature_token
        pr_id = pr.id
    assert token

    # GET public : la page de signature s'affiche, avec le nom de la personne.
    r = client.get(f"/kiosk/signer/{token}")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert f"Sig{suf}0" in body
    assert "k-sig-canvas" in body

    # POST : une vraie petite image PNG en dataURL.
    png_1px = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
               "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    r = client.post(f"/kiosk/signer/{token}", data={"signature_data": png_1px})
    assert r.status_code == 200
    assert "Merci" in r.get_data(as_text=True)

    with app.app_context():
        pr = db.session.get(PresenceActivite, pr_id)
        assert pr.signature_path              # signature enregistrée
        assert pr.signature_token is None     # jeton tué

    # Usage unique : le lien ne fonctionne plus.
    assert client.get(f"/kiosk/signer/{token}").status_code == 404


def test_signature_passe_par_la_facade_tunnel(app, client, admin_client):
    from app.models import PresenceActivite
    suf = uuid.uuid4().hex[:6]
    m = _seance_avec_presences(app, suf=suf, nb=1)
    admin_client.post(f"/activite/session/{m['sid']}/signature-liens")
    with app.app_context():
        token = PresenceActivite.query.filter_by(session_id=m["sid"]).one().signature_token

    app.config["KIOSK_PUBLIC_HOST"] = "public.exemple.fr"
    try:
        r = client.get(f"/kiosk/signer/{token}", headers={"Host": "public.exemple.fr"})
        assert r.status_code == 200
    finally:
        app.config["KIOSK_PUBLIC_HOST"] = ""


def test_lien_invalide_404(app, client):
    assert client.get("/kiosk/signer/nimportequoi").status_code == 404
