"""Export des contacts pour un outil d'e-mailing (Brevo, Mailjet…).

- seules les personnes avec un e-mail renseigné sont incluses ;
- les fiches anonymisées (RGPD) sont exclues ;
- un e-mail identique n'apparaît qu'une fois ;
- un rôle borné à un secteur ne voit que son périmètre.
"""
import csv
import io
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


def _lire_csv(data: bytes):
    texte = data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(texte), delimiter=";"))


def test_export_inclut_seulement_les_emails_renseignes(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add(Participant(nom=f"AvecMail{suf}", prenom="A", email=f"a{suf}@exemple.fr"))
        db.session.add(Participant(nom=f"SansMail{suf}", prenom="B", email=None))
        db.session.add(Participant(nom=f"MailVide{suf}", prenom="C", email=""))
        db.session.commit()

    r = admin_client.get("/participants/export-contacts-emailing.csv")
    assert r.status_code == 200
    lignes = _lire_csv(r.data)
    assert lignes[0] == ["EMAIL", "PRENOM", "NOM", "VILLE", "QUARTIER", "SECTEUR", "TYPE_PUBLIC"]
    noms = [l[2] for l in lignes[1:]]
    assert f"AvecMail{suf}" in noms
    assert f"SansMail{suf}" not in noms
    assert f"MailVide{suf}" not in noms


def test_fiches_anonymisees_exclues(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    from app.services.purge_rgpd import NOM_ANONYME
    with app.app_context():
        db.session.add(Participant(nom=NOM_ANONYME, prenom="X", email="anonyme@exemple.fr"))
        db.session.commit()

    r = admin_client.get("/participants/export-contacts-emailing.csv")
    lignes = _lire_csv(r.data)
    emails = [l[0] for l in lignes[1:]]
    assert "anonyme@exemple.fr" not in emails


def test_doublon_email_deduplique(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    suf = uuid.uuid4().hex[:6]
    email = f"partage{suf}@exemple.fr"
    with app.app_context():
        db.session.add(Participant(nom=f"Un{suf}", prenom="A", email=email))
        db.session.add(Participant(nom=f"Deux{suf}", prenom="B", email=email.upper()))
        db.session.commit()

    r = admin_client.get("/participants/export-contacts-emailing.csv")
    lignes = _lire_csv(r.data)
    emails = [l[0].lower() for l in lignes[1:]]
    assert emails.count(email.lower()) == 1


def test_role_borne_a_un_secteur_limite_a_son_perimetre(app):
    """responsable_secteur a participants:view_all (voit tout, exprès —
    seule l'édition est bornée) : on teste ici un rôle SANS view_all ni
    scope:all_secteurs, borné à son secteur via secteur_assigne."""
    from app.extensions import db
    from app.models import Participant, Permission, Role
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add(Participant(nom=f"DansSecteur{suf}", prenom="A", email=f"in{suf}@exemple.fr", created_secteur=f"Sect{suf}"))
        db.session.add(Participant(nom=f"AutreSecteur{suf}", prenom="B", email=f"out{suf}@exemple.fr", created_secteur=f"AutreSect{suf}"))
        db.session.commit()
        role = Role.query.filter_by(code="export_secteur_seul").first()
        if role is None:
            role = Role(code="export_secteur_seul", label="Export borné secteur")
            db.session.add(role)
            for code in ("dashboard:view", "participants:view"):
                perm = Permission.query.filter_by(code=code).first()
                if perm:
                    role.permissions.append(perm)
            db.session.commit()

    c = _login_role(app, f"export-secteur-{suf}@example.org", "export_secteur_seul", secteur=f"Sect{suf}")
    r = c.get("/participants/export-contacts-emailing.csv")
    lignes = _lire_csv(r.data)
    noms = [l[2] for l in lignes[1:]]
    assert f"DansSecteur{suf}" in noms
    assert f"AutreSecteur{suf}" not in noms


def test_export_reserve_a_participants_view(app):
    """Sans aucune permission de lecture participants, l'export est refusé."""
    from app.extensions import db
    from app.models import Permission, Role
    with app.app_context():
        role = Role.query.filter_by(code="export_sans_droits").first()
        if role is None:
            role = Role(code="export_sans_droits", label="Sans droits export")
            db.session.add(role)
            perm = Permission.query.filter_by(code="dashboard:view").first()
            if perm:
                role.permissions.append(perm)
            db.session.commit()
    c = _login_role(app, "export-refuse@example.org", "export_sans_droits")
    r = c.get("/participants/export-contacts-emailing.csv")
    assert r.status_code == 403
