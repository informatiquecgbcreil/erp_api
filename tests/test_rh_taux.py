"""Taux horaire de valorisation (permission benevolat:taux) + module RH
(réservé direction) + intégration SENACS + auto-attribution des permissions."""
import datetime as dt
import io
import uuid

import pytest
from flask import url_for


# ---------- utilisateurs de test ----------

def _login(app, email, password, role_code, secteur=None):
    """Crée (si besoin) un utilisateur avec le rôle donné et retourne un client connecté."""
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password(password)
            u.roles.append(Role.query.filter_by(code=role_code).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    r = c.post("/", data={"email": email, "password": password})
    assert r.status_code == 302
    return c


# ---------- taux horaire de valorisation ----------

def test_direction_regle_le_taux(app, admin_client):
    from app.extensions import db
    from app.models import InstanceSettings, AuditLog
    from app.services.benevolat import taux_horaire

    with app.app_context():
        with app.test_request_context():
            url = url_for("main.benevolat_taux_update")

    admin_client.post(url, data={"taux": "14,5"}, follow_redirects=True)
    with app.app_context():
        row = InstanceSettings.query.first()
        assert row is not None and row.benevolat_taux_horaire == 14.5
        assert taux_horaire() == 14.5                       # prioritaire sur la config
        assert AuditLog.query.filter_by(action="benevolat.taux").count() >= 1

    # taux hors bornes refusé
    admin_client.post(url, data={"taux": "0"}, follow_redirects=True)
    with app.app_context():
        assert InstanceSettings.query.first().benevolat_taux_horaire == 14.5


def test_taux_affecte_la_valorisation(app, admin_client):
    from app.extensions import db
    from app.models import Participant, BenevoleHeures
    from app.services.benevolat import stats_annee

    with app.app_context():
        p = Participant(nom=f"Val{uuid.uuid4().hex[:6]}", prenom="T")
        db.session.add(p)
        db.session.flush()
        db.session.add(BenevoleHeures(participant_id=p.id, date_action=dt.date(2053, 3, 3), heures=10))
        db.session.commit()
        stats = stats_annee(2053)
        assert stats["taux_horaire"] == 14.5                # réglé par le test précédent
        assert stats["valorisation"] == 145.0


def test_responsable_secteur_ne_peut_pas_regler_le_taux(app):
    c = _login(app, "resp-taux@example.org", "pw-resp-123", "responsable_secteur", secteur="Numérique")
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.benevolat_taux_update")
    r = c.post(url, data={"taux": "20"})
    assert r.status_code == 403


# ---------- module RH : accès réservé direction ----------

def test_rh_interdit_hors_direction(app):
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.rh")
    resp = _login(app, "resp-rh@example.org", "pw-resp-123", "responsable_secteur", secteur="Numérique")
    assert resp.get(url).status_code == 403
    fin = _login(app, "finance-rh@example.org", "pw-fin-123", "finance")
    assert fin.get(url).status_code == 403                  # finance exclu du RH


def test_rh_crud_et_affectation(app, admin_client):
    from app.extensions import db
    from app.models import Salarie, AuditLog

    nom = f"Sal{uuid.uuid4().hex[:6]}"
    with app.app_context():
        with app.test_request_context():
            url = url_for("main.rh_salarie_create")

    admin_client.post(url, data={
        "nom": nom, "prenom": "Awa", "poste": "Animatrice famille",
        "secteur": "Familles", "type_contrat": "cdi", "etp": "0,8",
        "salaire_brut_charge": "32000", "date_entree": "2051-01-01",
    }, follow_redirects=True)
    with app.app_context():
        s = Salarie.query.filter_by(nom=nom).first()
        assert s is not None
        assert s.secteur == "Familles" and s.poste == "Animatrice famille"
        assert s.etp == 0.8 and s.salaire_brut_charge == 32000.0
        assert AuditLog.query.filter_by(action="rh.salarie_create").count() >= 1
        sid = s.id
        with app.test_request_context():
            url_upd = url_for("main.rh_salarie_update", salarie_id=sid)

    # réaffectation secteur + poste + sortie
    admin_client.post(url_upd, data={
        "nom": nom, "prenom": "Awa", "poste": "Coordinatrice", "secteur": "Numérique",
        "type_contrat": "cdi", "etp": "1", "date_entree": "2051-01-01", "date_sortie": "2051-12-31",
    }, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Salarie, sid)
        assert s.secteur == "Numérique" and s.poste == "Coordinatrice"
        assert s.actif_sur(2051) is True
        assert s.actif_sur(2052) is False                  # sorti fin 2051


def test_rh_stats_et_export(app, admin_client):
    from app.extensions import db
    from app.models import Salarie
    from app.main.rh import stats_rh

    with app.app_context():
        db.session.add_all([
            Salarie(nom=f"A{uuid.uuid4().hex[:5]}", poste="Dir", secteur="Numérique",
                    etp=1.0, salaire_brut_charge=45000, date_entree=dt.date(2054, 1, 1),
                    date_sortie=dt.date(2054, 12, 31)),
            Salarie(nom=f"B{uuid.uuid4().hex[:5]}", poste="Anim", secteur="Numérique",
                    etp=0.5, salaire_brut_charge=15000, date_entree=dt.date(2054, 1, 1),
                    date_sortie=dt.date(2054, 12, 31)),
        ])
        db.session.commit()
        stats = stats_rh(2054)
        assert stats["nb_actifs"] == 2
        assert stats["total_etp"] == 1.5
        assert stats["masse_salariale"] == 60000.0
        assert stats["par_secteur"]["Numérique"]["etp"] == 1.5
        with app.test_request_context():
            url_x = url_for("main.rh_export_xlsx", annee=2054)

    r = admin_client.get(url_x)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")


def test_rh_import_csv_creation_et_maj(app, admin_client):
    from app.extensions import db
    from app.models import Salarie

    with app.app_context():
        with app.test_request_context():
            url = url_for("main.rh_import")

    csv1 = ("reference;nom;prenom;poste;secteur;contrat;etp;salaire;date_entree;date_sortie\n"
            "EXT-1;Diallo;Moussa;Animateur jeunesse;Numérique;cdd;0,5;16000;01/01/2055;31/12/2055\n"
            ";SansRef;Lea;Accueil;Familles;cdi;1;;2055-02-01;2055-12-31\n"
            ";;;;;;;;;\n")   # ligne sans nom : ignorée
    admin_client.post(url, data={"fichier": (io.BytesIO(csv1.encode("utf-8")), "rh.csv")},
                      content_type="multipart/form-data", follow_redirects=True)
    with app.app_context():
        moussa = Salarie.query.filter_by(source_ref="EXT-1").first()
        assert moussa is not None
        assert moussa.poste == "Animateur jeunesse" and moussa.etp == 0.5
        assert moussa.date_entree == dt.date(2055, 1, 1)
        assert Salarie.query.filter_by(nom="SansRef").count() == 1

    # réimport avec la même référence -> mise à jour, pas de doublon
    csv2 = ("reference;nom;poste;etp\nEXT-1;Diallo;Coordinateur jeunesse;1\n")
    admin_client.post(url, data={"fichier": (io.BytesIO(csv2.encode("utf-8")), "rh.csv")},
                      content_type="multipart/form-data", follow_redirects=True)
    with app.app_context():
        assert Salarie.query.filter_by(source_ref="EXT-1").count() == 1
        moussa = Salarie.query.filter_by(source_ref="EXT-1").first()
        assert moussa.poste == "Coordinateur jeunesse" and moussa.etp == 1.0


# ---------- intégration SENACS ----------

def test_senacs_emplois_inclut_les_salaries_rh(app):
    from app.extensions import db
    from app.models import Salarie
    from app.services.senacs import emplois_annee, finances_annee

    with app.app_context():
        db.session.add(Salarie(nom=f"Sen{uuid.uuid4().hex[:5]}", poste="Référente famille",
                               secteur="Familles", type_contrat="cdi", etp=0.7,
                               salaire_brut_charge=28000,
                               date_entree=dt.date(2056, 1, 1), date_sortie=dt.date(2056, 12, 31)))
        db.session.commit()

        emplois = emplois_annee(2056)
        assert emplois["nb_rh"] == 1
        assert emplois["rh_etp"] == 0.7
        assert emplois["masse_salariale"] == 28000.0
        assert emplois["etp_global"] == 0.7                # aucun poste manuel en 2056
        assert emplois["par_contrat"]["CDI"]["nb"] == 1

        finances = finances_annee(2056)
        assert finances["masse_salariale"] == 28000.0


# ---------- auto-attribution des nouvelles permissions ----------

def test_auto_grant_nouvelle_permission(app):
    """Une permission de PERMS_AUTO_GRANT créée après coup est accordée aux
    rôles cibles existants (cas d'une mise à jour en production)."""
    from app.extensions import db
    from app.models import Role, Permission
    from app.rbac import bootstrap_rbac

    with app.app_context():
        direction = Role.query.filter_by(code="direction").first()
        perm = Permission.query.filter_by(code="rh:view").first()
        assert perm is not None
        # simule une prod d'avant la fonctionnalité : rôle sans la perm, perm absente
        if perm in direction.permissions:
            direction.permissions = [p for p in direction.permissions if p.code != "rh:view"]
        db.session.delete(perm)
        db.session.commit()

        bootstrap_rbac()   # redémarrage applicatif

        direction = Role.query.filter_by(code="direction").first()
        assert any(p.code == "rh:view" for p in direction.permissions), \
            "rh:view doit être accordée à direction à sa première création"
