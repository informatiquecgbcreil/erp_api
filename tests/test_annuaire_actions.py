"""Annuaire des participants : actions groupées, familles, anonymisation.

- Ajout rapide de la date de naissance depuis la liste (sans ouvrir la fiche)
  et filtre « sans date de naissance ».
- Sélection par coches → regrouper en famille (foyer), avec la règle de
  prudence : jamais fusionner silencieusement deux familles existantes.
- Anonymisation groupée (permission dédiée) et suppression groupée
  (garde : présences dans d'autres secteurs).
- Réglages de la purge RGPD stockés en base, prioritaires sur l'environnement.
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


def _creer_participants(app, n, *, prefixe="Fam"):
    from app.extensions import db
    from app.models import Participant
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        pids = []
        for i in range(n):
            p = Participant(nom=f"{prefixe}{suf}", prenom=f"P{i}{suf}")
            db.session.add(p)
            db.session.flush()
            pids.append(p.id)
        db.session.commit()
        return pids, suf


# ---------- Date de naissance depuis l'annuaire ----------

def test_ajout_rapide_date_naissance(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    (pid,), _ = _creer_participants(app, 1, prefixe="Naiss")

    r = admin_client.post(f"/participants/{pid}/date-naissance",
                          data={"date_naissance": "1990-05-14", "retour": "/participants/?naissance=sans"})
    assert r.status_code == 302
    assert "/participants/?naissance=sans" in r.headers["Location"]
    with app.app_context():
        assert db.session.get(Participant, pid).date_naissance == dt.date(1990, 5, 14)

    # Date invalide : refusée proprement, rien n'est modifié.
    (pid2,), _ = _creer_participants(app, 1, prefixe="Naiss")
    admin_client.post(f"/participants/{pid2}/date-naissance", data={"date_naissance": "n-importe-quoi"})
    with app.app_context():
        assert db.session.get(Participant, pid2).date_naissance is None


def test_filtre_sans_date_naissance(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    (pid_sans,), suf = _creer_participants(app, 1, prefixe="SansDN")
    with app.app_context():
        avec = Participant(nom=f"AvecDN{suf}", prenom="X", date_naissance=dt.date(1980, 1, 1))
        db.session.add(avec)
        db.session.commit()

    body = admin_client.get(f"/participants/?naissance=sans&q=DN{suf}").get_data(as_text=True)
    assert f"SansDN{suf}" in body
    assert f"AvecDN{suf}" not in body
    # Le mini-formulaire d'ajout inline est proposé pour la fiche incomplète.
    assert f"/participants/{pid_sans}/date-naissance" in body


# ---------- Regroupement en famille (foyer) ----------

def test_regrouper_selection_en_famille(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    pids, _ = _creer_participants(app, 3)

    r = admin_client.post("/participants/actions-groupees",
                          data={"action": "famille", "pid": [str(i) for i in pids]})
    assert r.status_code == 302
    with app.app_context():
        foyers = {db.session.get(Participant, pid).foyer_id for pid in pids}
        assert len(foyers) == 1 and None not in foyers

    # Le badge famille apparaît dans l'annuaire.
    with app.app_context():
        nom = db.session.get(Participant, pids[0]).nom
    body = admin_client.get(f"/participants/?q={nom}").get_data(as_text=True)
    assert "👪" in body


def test_refus_fusion_de_deux_familles_existantes(app, admin_client):
    from app.extensions import db
    from app.models import Foyer, Participant
    pids, _ = _creer_participants(app, 2)
    with app.app_context():
        f1, f2 = Foyer(nom="Famille A"), Foyer(nom="Famille B")
        db.session.add_all([f1, f2])
        db.session.flush()
        db.session.get(Participant, pids[0]).foyer_id = f1.id
        db.session.get(Participant, pids[1]).foyer_id = f2.id
        db.session.commit()
        f1_id, f2_id = f1.id, f2.id

    admin_client.post("/participants/actions-groupees",
                      data={"action": "famille", "pid": [str(i) for i in pids]})
    with app.app_context():
        # Rien n'a bougé : pas de fusion silencieuse d'adhésions familiales.
        assert db.session.get(Participant, pids[0]).foyer_id == f1_id
        assert db.session.get(Participant, pids[1]).foyer_id == f2_id


def test_famille_rejoint_le_foyer_existant(app, admin_client):
    from app.extensions import db
    from app.models import Foyer, Participant
    pids, _ = _creer_participants(app, 3)
    with app.app_context():
        foyer = Foyer(nom="Famille Existante")
        db.session.add(foyer)
        db.session.flush()
        db.session.get(Participant, pids[0]).foyer_id = foyer.id
        db.session.commit()
        foyer_id = foyer.id

    admin_client.post("/participants/actions-groupees",
                      data={"action": "famille", "pid": [str(i) for i in pids]})
    with app.app_context():
        assert all(db.session.get(Participant, pid).foyer_id == foyer_id for pid in pids)


# ---------- Anonymisation groupée ----------

def test_anonymisation_groupee(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    pids, suf = _creer_participants(app, 2, prefixe="Anon")
    with app.app_context():
        p = db.session.get(Participant, pids[0])
        p.email, p.telephone = "secret@ex.org", "0600000000"
        db.session.commit()

    r = admin_client.post("/participants/actions-groupees",
                          data={"action": "anonymiser", "pid": [str(i) for i in pids]})
    assert r.status_code == 302
    with app.app_context():
        for pid in pids:
            p = db.session.get(Participant, pid)
            assert p.nom == "ANONYME"
            assert p.prenom == f"P{pid}"
            assert p.email is None and p.telephone is None


def _role_sur_mesure(app, perms: list[str]) -> str:
    """Crée un rôle de test doté uniquement des permissions demandées."""
    from app.extensions import db
    from app.models import Permission, Role
    code = f"role-{uuid.uuid4().hex[:8]}"
    with app.app_context():
        role = Role(code=code, label=code)
        db.session.add(role)
        for p in perms:
            perm = Permission.query.filter_by(code=p).first()
            if perm is None:
                perm = Permission(code=p, label=p)
                db.session.add(perm)
            role.permissions.append(perm)
        db.session.commit()
    return code


def test_anonymisation_groupee_exige_la_permission(app):
    """Un rôle sans participants:anonymize est refusé (403)."""
    suf = uuid.uuid4().hex[:6]
    role = _role_sur_mesure(app, ["participants:view"])
    c = _login_role(app, f"lecteur-{suf}@ex.org", role)
    pids, _ = _creer_participants(app, 1)
    r = c.post("/participants/actions-groupees",
               data={"action": "anonymiser", "pid": [str(pids[0])]})
    assert r.status_code == 403


# ---------- Suppression groupée ----------

def test_suppression_groupee_avec_garde_secteur(app):
    """Un agent borné à son secteur (sans participants:view_all) ne supprime
    pas une personne suivie ailleurs : elle est protégée, les autres sont
    supprimées."""
    from app.extensions import db
    from app.models import (AtelierActivite, Participant, PresenceActivite,
                            SessionActivite)
    suf = uuid.uuid4().hex[:6]
    mien, autre = f"Num{suf}", f"Fam{suf}"
    pids, _ = _creer_participants(app, 2, prefixe="Suppr")
    with app.app_context():
        at = AtelierActivite(nom=f"Ailleurs{suf}", secteur=autre, type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=autre, session_type="COLLECTIF",
                            date_session=dt.date.today())
        db.session.add(s)
        db.session.flush()
        # pids[0] est présent dans l'AUTRE secteur : protégé.
        db.session.add(PresenceActivite(session_id=s.id, participant_id=pids[0]))
        db.session.commit()

    role = _role_sur_mesure(app, ["participants:view", "participants:delete"])
    c = _login_role(app, f"resp-{suf}@ex.org", role, secteur=mien)
    r = c.post("/participants/actions-groupees",
               data={"action": "supprimer", "pid": [str(i) for i in pids]})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, pids[0]) is not None   # protégé
        assert db.session.get(Participant, pids[1]) is None       # supprimé


def test_export_selection_csv(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    pids, suf = _creer_participants(app, 2, prefixe="Export")
    with app.app_context():
        p = db.session.get(Participant, pids[0])
        p.date_naissance = dt.date(1975, 3, 2)
        db.session.commit()

    r = admin_client.post("/participants/actions-groupees",
                          data={"action": "export", "pid": [str(i) for i in pids]})
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]
    body = r.get_data(as_text=True)
    assert f"Export{suf}" in body
    assert "02/03/1975" in body


def test_selection_vide_redirige_avec_message(app, admin_client):
    r = admin_client.post("/participants/actions-groupees", data={"action": "famille"})
    assert r.status_code == 302


# ---------- Anonymisation visible sur la fiche ----------

def test_fiche_participant_affiche_le_bloc_anonymisation(app, admin_client):
    pids, _ = _creer_participants(app, 1, prefixe="Fiche")
    body = admin_client.get(f"/participants/{pids[0]}/edit").get_data(as_text=True)
    assert "Anonymisation (RGPD)" in body
    assert f"/participants/{pids[0]}/anonymize" in body


# ---------- Réglages de la purge RGPD ----------

def test_reglages_purge_stockes_en_base_et_prioritaires(app, admin_client):
    from app.extensions import db
    from app.models import InstanceSettings

    r = admin_client.post("/controle/purge-rgpd/reglages",
                          data={"annees": "5", "auto": "1"})
    assert r.status_code == 302
    with app.app_context():
        from app.services.purge_rgpd import annees_inactivite, purge_auto_active
        assert annees_inactivite() == 5
        assert purge_auto_active() is True
        reglages = InstanceSettings.query.first()
        assert reglages.purge_rgpd_annees == 5
        assert reglages.purge_rgpd_auto is True

    # Désactivation de l'automatique + délai hors bornes refusé.
    admin_client.post("/controle/purge-rgpd/reglages", data={"annees": "3"})
    with app.app_context():
        from app.services.purge_rgpd import purge_auto_active
        assert purge_auto_active() is False

    admin_client.post("/controle/purge-rgpd/reglages", data={"annees": "42", "auto": "1"})
    with app.app_context():
        from app.services.purge_rgpd import annees_inactivite
        assert annees_inactivite() == 3  # inchangé


def test_page_purge_affiche_les_criteres(app, admin_client):
    body = admin_client.get("/controle/purge-rgpd").get_data(as_text=True)
    assert "Les critères, en détail" in body
    assert "Régler les critères" in body
