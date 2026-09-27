"""Chiffres remis aux financeurs : seules les venues réelles de séances tenues
comptent (audit 5.3), le quartier inconnu d'une ville à QPV n'est pas « Hors
QPV » (5.7), et la page SENACS ne fait plus une requête par présence (5.8).

Scénario de l'audit : en réalité 3 personnes et 5 présences ; SENACS
annonçait 6 participants et 8 participations, et un atelier affichait
3 séances « réalisées » dont une annulée et une seulement prévue.
"""
import datetime as dt
import uuid

from conftest import ADMIN_EMAIL


def creer_scenario(app) -> dict:
    """Atelier de l'année en cours : 2 séances tenues, 1 annulée, 1 à venir.

    Venues réelles : A et B aux deux séances tenues, C à la première,
    soit 5 présences et 3 personnes. Parasites : D absente excusée,
    E pointée sur la séance annulée, F pointée à l'avance sur la séance
    à venir.
    """
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    aujourd_hui = dt.date.today()
    debut_annee = dt.date(aujourd_hui.year, 1, 1)
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        at = AtelierActivite(nom=f"Senacs{suf}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()

        def seance(jour, statut="realisee"):
            s = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                                date_session=jour, heure_debut="14:00", heure_fin="16:00", statut=statut)
            db.session.add(s)
            db.session.flush()
            return s

        t1 = seance(debut_annee)
        t2 = seance(min(debut_annee + dt.timedelta(days=1), aujourd_hui))
        annulee = seance(debut_annee, statut="annulee")
        a_venir = seance(aujourd_hui + dt.timedelta(days=1))
        p = {}
        for nom in "ABCDEF":
            p[nom] = Participant(nom=f"{nom}{suf}", prenom="T")
            db.session.add(p[nom])
        db.session.flush()
        for s, qui, type_ in [(t1, "A", "present"), (t1, "B", "retard"), (t1, "C", "present"),
                              (t2, "A", "present"), (t2, "B", "present"),
                              (t2, "D", "absent_excuse"), (annulee, "E", "present"),
                              (a_venir, "F", "present")]:
            db.session.add(PresenceActivite(session_id=s.id, participant_id=p[qui].id, presence_type=type_))
        db.session.commit()
        return {"annee": aujourd_hui.year, "atelier": at.nom, "atelier_id": at.id,
                "debut": debut_annee, "fin": dt.date(aujourd_hui.year, 12, 31)}


def test_senacs_tableau_actions(app):
    from app.services.senacs import tableau_actions
    sc = creer_scenario(app)
    with app.app_context():
        ligne = {l["atelier"]: l for l in tableau_actions(sc["annee"])}[sc["atelier"]]
    assert (ligne["participations"], ligne["participants_uniques"], ligne["seances"]) == (5, 3, 2)
    assert ligne["heures_face_public"] == 4.0


def test_senacs_publics_globaux(app):
    """Les chiffres globaux bougent exactement de 3 personnes et 5 venues."""
    from app.services.senacs import publics_annee
    annee = dt.date.today().year
    with app.app_context():
        avant = publics_annee(annee)
    creer_scenario(app)
    with app.app_context():
        apres = publics_annee(annee)
    assert apres["participants_uniques"] - avant["participants_uniques"] == 3
    assert apres["participations"] - avant["participations"] == 5


def test_evenementiel_ignore_absences_et_annulations(app):
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    from app.services.senacs import evenementiel_annee
    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        at = AtelierActivite(nom=f"Fete{suf}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        fete = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                               date_session=dt.date(1989, 6, 21), est_evenement=True)
        annulee = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                                  date_session=dt.date(1989, 6, 22), est_evenement=True, statut="annulee")
        db.session.add_all([fete, annulee])
        db.session.flush()
        gens = [Participant(nom=f"F{i}{suf}", prenom="T") for i in range(3)]
        db.session.add_all(gens)
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=fete.id, participant_id=gens[0].id),
            PresenceActivite(session_id=fete.id, participant_id=gens[1].id, presence_type="absent_excuse"),
            PresenceActivite(session_id=annulee.id, participant_id=gens[2].id),
        ])
        db.session.commit()
        ev = evenementiel_annee(1989)
    assert (ev["nb_evenements"], ev["participations"], ev["participants_uniques"]) == (1, 1, 1)


def test_statistiques_d_impact(app):
    from flask_login import login_user
    from app.models import User
    from app.statsimpact.engine import StatsFilters, compute_volume_activity_stats
    sc = creer_scenario(app)
    with app.test_request_context():
        login_user(User.query.filter_by(email=ADMIN_EMAIL).one())
        stats = compute_volume_activity_stats(StatsFilters(atelier_ids=[sc["atelier_id"]],
                                                           date_from=sc["debut"], date_to=sc["fin"]))
    assert (stats["kpi"]["sessions"], stats["kpi"]["presences"], stats["kpi"]["uniques"]) == (2, 5, 3)


def test_cout_unitaire(app):
    from app.main.couts import mesures_activite
    sc = creer_scenario(app)
    with app.app_context():
        m = mesures_activite([sc["atelier_id"]], sc["debut"], sc["fin"])
    assert (m["sessions"], m["presences"], m["uniques"], m["heures"]) == (2, 5, 3, 4.0)


def test_indicateurs_de_projet(app):
    from app.services.indicators import _participants_metrics
    sc = creer_scenario(app)
    with app.app_context():
        m = _participants_metrics([sc["atelier_id"]], sc["debut"], sc["fin"])
    assert (m["sessions_totales"], m["presences_totales"], m["participants_uniques"]) == (2, 5, 3)
    assert m["recurrence_2plus"] == 2  # A et B


# ---------------------------------------------------------------------------
# 5.7 : quartier inconnu
# ---------------------------------------------------------------------------

def test_quartier_inconnu_dans_une_ville_a_qpv(app):
    from app.extensions import db
    from app.models import Participant, Quartier
    from app.services.senacs import _bucket_quartier
    suf = uuid.uuid4().hex[:6]
    ville_qpv, ville_sans = f"Qpville{suf}", f"Tranquille{suf}"
    with app.app_context():
        db.session.add_all([
            Quartier(ville=ville_qpv, nom=f"Centre{suf}", qpv="Hauts de Creil"),
            Quartier(ville=ville_sans, nom=f"Bourg{suf}"),
        ])
        db.session.commit()
        assert _bucket_quartier(Participant(nom="X", prenom="Y", ville=ville_qpv.upper())) == "Non renseigné"
        assert _bucket_quartier(Participant(nom="X", prenom="Y", ville=ville_sans)) == "Hors QPV"
        assert _bucket_quartier(Participant(nom="X", prenom="Y", ville="")) == "Non renseigné"


# ---------------------------------------------------------------------------
# 5.8 : nombre de requêtes indépendant du nombre de présences
# ---------------------------------------------------------------------------

def test_senacs_nombre_de_requetes_borne(app):
    """Avant : une requête par présence (12 000 requêtes, 10 s pour 104 000
    présences). Doubler les présences ne doit plus ajouter de requêtes."""
    from sqlalchemy import event
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    from app.services.senacs import publics_annee, tableau_actions

    def mesurer():
        compte = {"n": 0}

        def compter(*_a, **_k):
            compte["n"] += 1
        with app.app_context():
            db.session.expunge_all()
            event.listen(db.engine, "before_cursor_execute", compter)
            try:
                publics_annee(1988)
                tableau_actions(1988)
            finally:
                event.remove(db.engine, "before_cursor_execute", compter)
        return compte["n"]

    def ajouter(n):
        suf = uuid.uuid4().hex[:6]
        with app.app_context():
            at = AtelierActivite(nom=f"Perf{suf}", secteur="Numérique")
            db.session.add(at)
            db.session.flush()
            s = SessionActivite(atelier_id=at.id, secteur="Numérique", session_type="COLLECTIF",
                                date_session=dt.date(1988, 3, 1), heure_debut="10:00", heure_fin="11:00")
            gens = [Participant(nom=f"Perf{i}{suf}", prenom="T") for i in range(n)]
            db.session.add(s)
            db.session.add_all(gens)
            db.session.flush()
            db.session.add_all([PresenceActivite(session_id=s.id, participant_id=g.id) for g in gens])
            db.session.commit()

    ajouter(20)
    petit = mesurer()
    ajouter(200)
    grand = mesurer()
    assert grand <= petit + 2, (petit, grand)
