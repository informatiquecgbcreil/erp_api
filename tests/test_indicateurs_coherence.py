"""Mêmes règles de comptage partout (audit 5.3, vérification demandée sur
tous les bilans et exports) : une absence excusée n'est pas une venue ; une
séance annulée ou à venir n'a pas eu lieu. SENACS, bilans, statistiques et
coûts appliquaient déjà ces règles (chantier 3) ; la comparaison des
secteurs, le taux de remplissage, l'espace direction, le tableau de bord, la
fiche quartier et l'export CSV des présences les appliquent désormais."""
import csv
import datetime as dt
import io
import uuid

import pytest


@pytest.fixture
def jeu(app):
    """Secteur neuf, année passée : 3 venues réelles (dont un retard), 1
    absence excusée, 1 présence sur une séance annulée, 1 sur une séance à
    venir."""
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    sec = f"Coh{uuid.uuid4().hex[:6]}"
    with app.app_context():
        atelier = AtelierActivite(nom=f"Atelier {sec}", secteur=sec, type_atelier="COLLECTIF", is_active=True)
        db.session.add(atelier)
        db.session.flush()

        def seance(jour, statut="realisee"):
            s = SessionActivite(atelier_id=atelier.id, secteur=sec, session_type="COLLECTIF",
                                date_session=jour, statut=statut, capacite=10)
            db.session.add(s)
            db.session.flush()
            return s

        tenue = seance(dt.date(2021, 3, 3))
        annulee = seance(dt.date(2021, 3, 10), statut="annulee")
        a_venir = seance(dt.date.today() + dt.timedelta(days=30))
        personnes = []
        for i in range(5):
            p = Participant(nom=f"Coh{i}{uuid.uuid4().hex[:4]}", prenom="T", created_secteur=sec)
            db.session.add(p)
            db.session.flush()
            personnes.append(p)
        for p, type_ in zip(personnes[:4], ("present", "present", "retard", "absent_excuse")):
            db.session.add(PresenceActivite(session_id=tenue.id, participant_id=p.id, presence_type=type_))
        db.session.add(PresenceActivite(session_id=annulee.id, participant_id=personnes[4].id))
        db.session.add(PresenceActivite(session_id=a_venir.id, participant_id=personnes[4].id))
        db.session.commit()
        return {"secteur": sec, "atelier_id": atelier.id}


def test_comparaison_des_secteurs(app, jeu):
    from app.main.comparaison import _activite_par_secteur
    with app.app_context():
        activite = _activite_par_secteur(2021, jeu["secteur"])[jeu["secteur"]]
    assert activite == {"sessions": 1.0, "presences": 3.0}


def test_taux_de_remplissage(app, jeu):
    from flask_login import login_user
    from conftest import ADMIN_EMAIL
    from app.models import User
    from app.statsimpact.engine import normalize_filters
    from app.statsimpact.occupancy import compute_occupancy_stats
    with app.test_request_context():
        admin = User.query.filter_by(email=ADMIN_EMAIL).one()
        login_user(admin)
        flt = normalize_filters({"secteur": jeu["secteur"], "date_from": "2021-01-01", "date_to": "2021-12-31"},
                                user=admin)
        stats = compute_occupancy_stats(flt)
    assert stats["collective_sessions"] == 1 and stats["collective_presences"] == 3


def test_export_csv_des_presences(app, admin_client, jeu):
    r = admin_client.get("/stats-impact/magatomatique.csv", query_string={
        "secteur": jeu["secteur"], "date_from": "2021-01-01", "date_to": "2021-12-31"})
    lignes = list(csv.reader(io.StringIO(r.get_data(as_text=True)), delimiter=";"))
    assert len(lignes) - 1 == 3


def test_taux_qpv_sans_les_quartiers_inconnus(app):
    """Audit 5.7 dans les indicateurs de projet : l'habitant d'une ville qui
    compte un QPV, sans quartier, ne fait plus baisser le taux QPV."""
    from app.extensions import db
    from app.models import Participant, Quartier
    from app.services.indicators import _add_demography_metrics
    ville = f"Villeqpv{uuid.uuid4().hex[:5]}"
    with app.app_context():
        qpv = Quartier(ville=ville, nom="Cité", qpv="QPV Cité", is_qpv=True)
        hors = Quartier(ville=ville, nom="Centre", is_qpv=False)
        db.session.add_all([qpv, hors])
        db.session.flush()
        ids = []
        for quartier, v in ((qpv, ville), (hors, ville), (None, ville), (None, ville)):
            p = Participant(nom=f"Q{uuid.uuid4().hex[:6]}", prenom="T", ville=v,
                            quartier_id=quartier.id if quartier else None)
            db.session.add(p)
            db.session.flush()
            ids.append(p.id)
        db.session.commit()
        out = {}
        _add_demography_metrics(out, ids)
    assert out["participants_qpv"] == 1 and out["participants_qpv_inconnu"] == 2
    assert out["taux_qpv"] == 50.0
