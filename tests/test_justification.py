"""Automatisation de la justification financeurs :
1. feuille de temps par subvention (séances + créneaux projet + préparation) ;
3. radar des échéances (bilan à rendre, versement attendu) dans À traiter ;
2. dossier de justification (démographie, dépenses imputées, bénévolat).
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


def _montage(app, *, suf, annee=None):
    """Subvention + atelier financé + 2 séances du mois courant + présences."""
    from app.extensions import db
    from app.models import (AtelierActivite, Participant, PresenceActivite,
                            SessionActivite, Subvention, SubventionAtelier)
    today = dt.date.today()
    annee = annee or today.year
    with app.app_context():
        sect = f"Just{suf}"
        sub = Subvention(nom=f"ProjetASL{suf}", secteur=sect, annee_exercice=annee,
                         financeur="CAF", montant_attribue=6000.0, statut_cycle="accordee")
        at = AtelierActivite(nom=f"ASL{suf}", secteur=sect, type_atelier="COLLECTIF")
        db.session.add_all([sub, at]); db.session.flush()
        db.session.add(SubventionAtelier(subvention_id=sub.id, atelier_id=at.id, poids_pct=100.0))
        s1 = SessionActivite(atelier_id=at.id, secteur=sect, session_type="COLLECTIF",
                             date_session=today.replace(day=5), heure_debut="14:00", heure_fin="16:00")
        s2 = SessionActivite(atelier_id=at.id, secteur=sect, session_type="COLLECTIF",
                             date_session=today.replace(day=12), heure_debut="10:00", heure_fin="11:30")
        db.session.add_all([s1, s2]); db.session.flush()
        p = Participant(nom=f"Benef{suf}", prenom="A", genre="Femme",
                        date_naissance=dt.date(today.year - 30, 1, 1))
        db.session.add(p); db.session.flush()
        db.session.add(PresenceActivite(session_id=s1.id, participant_id=p.id))
        db.session.commit()
        return {"sub_id": sub.id, "atelier_id": at.id, "secteur": sect, "pid": p.id}


# ---------- 1. Feuille de temps ----------

def test_feuille_de_temps_seances_et_totaux(app, admin_client):
    suf = uuid.uuid4().hex[:6]
    m = _montage(app, suf=suf)
    today = dt.date.today()
    du, au = today.replace(day=1), today.replace(day=28)

    r = admin_client.get(f"/subvention/{m['sub_id']}/feuille-de-temps?du={du}&au={au}")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert f"ASL{suf}" in body
    assert "2h00" in body          # séance 14:00-16:00
    assert "1h30" in body          # séance 10:00-11:30
    assert "3.50 h" in body        # total face-à-face


def test_feuille_de_temps_preparation_forfaitaire(app, admin_client):
    suf = uuid.uuid4().hex[:6]
    m = _montage(app, suf=suf)
    today = dt.date.today()
    du, au = today.replace(day=1), today.replace(day=28)

    body = admin_client.get(
        f"/subvention/{m['sub_id']}/feuille-de-temps?du={du}&au={au}&prep=30"
    ).get_data(as_text=True)
    assert "Préparation forfaitaire" in body
    assert "1.00 h" in body        # 30 min × 2 séances
    assert "4.50 h" in body        # total 3,5 + 1


def test_feuille_de_temps_inclut_les_creneaux_du_projet(app, admin_client):
    from app.extensions import db
    from app.models import AgendaCreneau, User
    suf = uuid.uuid4().hex[:6]
    m = _montage(app, suf=suf)
    today = dt.date.today()
    with app.app_context():
        uid = User.query.first().id
        db.session.add(AgendaCreneau(
            user_id=uid, type_creneau="reunion", titre=f"CoPil{suf}",
            date_creneau=today.replace(day=8), heure_debut="09:00", heure_fin="11:00",
            subvention_id=m["sub_id"],
        ))
        # Créneau NON rattaché : ne doit pas apparaître
        db.session.add(AgendaCreneau(
            user_id=uid, type_creneau="reunion", titre=f"HorsProjet{suf}",
            date_creneau=today.replace(day=8), heure_debut="09:00", heure_fin="10:00",
        ))
        db.session.commit()

    du, au = today.replace(day=1), today.replace(day=28)
    body = admin_client.get(
        f"/subvention/{m['sub_id']}/feuille-de-temps?du={du}&au={au}"
    ).get_data(as_text=True)
    assert f"CoPil{suf}" in body
    assert f"HorsProjet{suf}" not in body
    assert "5.50 h" in body        # 3,5 face-à-face + 2 créneau


def test_creneau_rattache_depuis_mon_agenda(app):
    from app.models import AgendaCreneau, User
    suf = uuid.uuid4().hex[:6]
    m = _montage(app, suf=suf)
    c = _login_role(app, f"ft-{suf}@ex.org", "responsable_secteur", secteur=m["secteur"])
    r = c.post("/mon-agenda/creneau", data={
        "type_creneau": "preparation", "titre": f"PrepProjet{suf}",
        "date_creneau": dt.date.today().isoformat(),
        "heure_debut": "08:00", "heure_fin": "09:00",
        "subvention_id": str(m["sub_id"]), "repeter_semaines": "0",
    })
    assert r.status_code == 302
    with app.app_context():
        row = AgendaCreneau.query.filter_by(titre=f"PrepProjet{suf}").one()
        assert row.subvention_id == m["sub_id"]
        assert row.duree_minutes == 60


# ---------- 3. Radar des échéances ----------

def test_radar_bilan_a_rendre_dans_a_traiter(app, admin_client):
    from app.extensions import db
    from app.models import Subvention
    suf = uuid.uuid4().hex[:6]
    today = dt.date.today()
    with app.app_context():
        db.session.add(Subvention(nom=f"BilanProche{suf}", secteur=f"R{suf}", annee_exercice=today.year,
                                  financeur="Ville", statut_cycle="versee",
                                  date_bilan_prevu=today + dt.timedelta(days=10)))
        db.session.add(Subvention(nom=f"BilanRetard{suf}", secteur=f"R{suf}", annee_exercice=today.year,
                                  statut_cycle="versee",
                                  date_bilan_prevu=today - dt.timedelta(days=3)))
        db.session.add(Subvention(nom=f"BilanLoin{suf}", secteur=f"R{suf}", annee_exercice=today.year,
                                  statut_cycle="versee",
                                  date_bilan_prevu=today + dt.timedelta(days=200)))
        db.session.add(Subvention(nom=f"Soldee{suf}", secteur=f"R{suf}", annee_exercice=today.year,
                                  statut_cycle="soldee",
                                  date_bilan_prevu=today + dt.timedelta(days=5)))
        db.session.commit()

    body = admin_client.get("/suivi-rappels").get_data(as_text=True)
    assert f"BilanProche{suf}" in body
    assert f"BilanRetard{suf}" in body
    assert "Échéance dépassée" in body
    assert f"BilanLoin{suf}" not in body       # hors horizon 45 j
    assert f"Soldee{suf}" not in body          # cycle terminé


def test_radar_versement_attendu_non_recu(app, admin_client):
    from app.extensions import db
    from app.models import Subvention
    suf = uuid.uuid4().hex[:6]
    today = dt.date.today()
    with app.app_context():
        db.session.add(Subvention(nom=f"VersManque{suf}", secteur=f"V{suf}", annee_exercice=today.year,
                                  statut_cycle="accordee", montant_attribue=5000.0, montant_recu=0.0,
                                  date_versement_prevu=today - dt.timedelta(days=15)))
        db.session.add(Subvention(nom=f"VersOk{suf}", secteur=f"V{suf}", annee_exercice=today.year,
                                  statut_cycle="versee", montant_attribue=5000.0, montant_recu=5000.0,
                                  date_versement_prevu=today - dt.timedelta(days=15)))
        db.session.commit()

    body = admin_client.get("/suivi-rappels").get_data(as_text=True)
    assert f"VersManque{suf}" in body
    assert f"VersOk{suf}" not in body


# ---------- 2. Dossier de justification ----------

def test_dossier_demographie_depenses_benevolat(app, admin_client):
    from app.extensions import db
    from app.models import BenevoleHeures, Depense, DepenseAffectation, Participant
    suf = uuid.uuid4().hex[:6]
    m = _montage(app, suf=suf)
    today = dt.date.today()
    with app.app_context():
        # Dépense imputée à la subvention
        dep = Depense(libelle=f"Fournitures{suf}", montant=180.0, date_paiement=today)
        db.session.add(dep); db.session.flush()
        db.session.add(DepenseAffectation(depense_id=dep.id, subvention_id=m["sub_id"], montant=180.0))
        # Bénévolat sur le secteur
        benevole = Participant(nom=f"Benevole{suf}", prenom="B", est_benevole=True)
        db.session.add(benevole); db.session.flush()
        db.session.add(BenevoleHeures(participant_id=benevole.id, date_action=today,
                                      heures=6.0, secteur=m["secteur"]))
        db.session.commit()

    body = admin_client.get(f"/subvention/{m['sub_id']}/justificatif").get_data(as_text=True)
    # Démographie (1 participante unique de 30 ans)
    assert "Publics touchés" in body
    assert "Femme" in body
    assert "26-59 ans" in body
    # Dépenses imputées
    assert f"Fournitures{suf}" in body
    assert "180.00" in body
    # Bénévolat valorisé
    assert "Contributions volontaires" in body
    assert "6.0 h" in body
