"""Tests de non-régression pour les corrections d'audit métier.

Couvre :
1. SENACS date les séances individuelles (RDV) par rdv_date, pas created_at.
2. L'âge des bilans/indicateurs est figé à la fin de la période analysée.
3. Les KPI de volume excluent les séances annulées.
4. La purge RGPD tient compte des cotisations comme activité récente.
5. Le bénévolat respecte le filtre secteur pour le nombre de bénévoles déclarés.
6. La détection de doublons retrouve une initiale accentuée.
"""
import uuid
from datetime import date, datetime

from flask_login import login_user


def _suffixe_alpha() -> str:
    return "".join(c for c in uuid.uuid4().hex if c.isalpha())[:6] or "abcdef"


# --------------------------------------------------------------------------
# 1. SENACS : séances individuelles datées par rdv_date
# --------------------------------------------------------------------------

def test_senacs_rdv_compte_sur_rdv_date(app):
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
        from app.services.senacs import annees_disponibles, tableau_actions

        suf = _suffixe_alpha()
        at_nom = f"Acces droits {suf}"
        p = Participant(nom=f"Rdv{suf}", prenom="Test",
                        date_naissance=date(1990, 1, 1), genre="Femme")
        at = AtelierActivite(nom=at_nom, secteur="Numérique", type_atelier="INDIVIDUEL_MENSUEL")
        db.session.add_all([p, at])
        db.session.flush()

        # RDV du 15/03/2022, mais saisi (created_at) le 10/01/2024.
        s = SessionActivite(atelier_id=at.id, secteur="Numérique",
                            session_type="INDIVIDUEL_MENSUEL",
                            rdv_date=date(2022, 3, 15), date_session=None,
                            duree_minutes=45)
        s.created_at = datetime(2024, 1, 10, 9, 0, 0)
        db.session.add(s)
        db.session.flush()
        pr = PresenceActivite(session_id=s.id, participant_id=p.id)
        pr.created_at = datetime(2024, 1, 10, 9, 0, 0)
        db.session.add(pr)
        db.session.commit()

        rows_2022 = {r["atelier"]: r for r in tableau_actions(2022)}
        rows_2024 = {r["atelier"]: r for r in tableau_actions(2024)}

        assert at_nom in rows_2022, "la séance RDV doit être rattachée à 2022 (rdv_date)"
        assert rows_2022[at_nom]["participations"] == 1
        assert rows_2022[at_nom]["seances"] == 1
        assert at_nom not in rows_2024, "elle ne doit pas être rattachée à l'année de saisie"
        assert 2022 in annees_disponibles()


# --------------------------------------------------------------------------
# 2. Âge figé à la fin de période (cohérence bilans passés / SENACS)
# --------------------------------------------------------------------------

def test_age_fige_a_la_periode(app):
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
        from app.services.indicators import _participants_metrics

        suf = _suffixe_alpha()
        # Né le 01/01/2005 : mineur (15 ans) fin 2020, mais majeur aujourd'hui.
        p = Participant(nom=f"Age{suf}", prenom="Jeune",
                        date_naissance=date(2005, 1, 1), genre="Homme")
        at = AtelierActivite(nom=f"Age {suf}", secteur="Jeunesse")
        db.session.add_all([p, at])
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur="Jeunesse", session_type="COLLECTIF",
                            date_session=date(2020, 6, 1), heure_debut="10:00", heure_fin="12:00")
        db.session.add(s)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=s.id, participant_id=p.id))
        db.session.commit()

        assert p.age_au(date(2020, 12, 31)) == 15
        assert p.age_au(date(2023, 12, 31)) == 18

        metrics = _participants_metrics([at.id], date(2020, 1, 1), date(2020, 12, 31))
        assert metrics["participants_mineurs"] == 1, "âge à la période, pas âge d'aujourd'hui"
        assert metrics["participants_18_25"] == 0
        assert metrics["age_moyen"] == 15.0


# --------------------------------------------------------------------------
# 3. Volume : les séances annulées ne comptent pas
# --------------------------------------------------------------------------

def test_volume_exclut_seances_annulees(app):
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite, User
        from app.statsimpact.engine import StatsFilters, compute_volume_activity_stats

        suf = _suffixe_alpha()
        secteur = f"SecVol{suf}"
        at = AtelierActivite(nom=f"Vol {suf}", secteur=secteur)
        p1 = Participant(nom=f"Vun{suf}", prenom="a")
        p2 = Participant(nom=f"Vdeux{suf}", prenom="b")
        db.session.add_all([at, p1, p2])
        db.session.flush()
        s_ok = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                               date_session=date(2023, 5, 1), heure_debut="10:00",
                               heure_fin="12:00", statut="realisee")
        s_no = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                               date_session=date(2023, 5, 8), heure_debut="10:00",
                               heure_fin="12:00", statut="annulee")
        db.session.add_all([s_ok, s_no])
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=s_ok.id, participant_id=p1.id),
            PresenceActivite(session_id=s_no.id, participant_id=p2.id),
        ])
        db.session.commit()

        with app.test_request_context():
            login_user(User.query.first())
            flt = StatsFilters(secteur=secteur, atelier_ids=[at.id],
                               date_from=date(2023, 1, 1), date_to=date(2023, 12, 31))
            res = compute_volume_activity_stats(flt)

        kpi = res["kpi"]
        assert kpi["sessions"] == 1, "la séance annulée ne doit pas compter"
        assert kpi["presences"] == 1, "la présence sur une séance annulée est écartée"
        assert kpi["uniques"] == 1
        assert kpi["hours_animator"] == 2.0, "seule la séance réalisée (2h) compte"


# --------------------------------------------------------------------------
# 4. Purge RGPD : une cotisation récente garde la fiche active
# --------------------------------------------------------------------------

def test_purge_tient_compte_des_cotisations(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, Participant
        from app.services.purge_rgpd import participants_inactifs

        suf = _suffixe_alpha()
        vieux = datetime(2017, 1, 1)

        adherent = Participant(nom=f"Adh{suf}", prenom="Soutien")
        temoin = Participant(nom=f"Ctrl{suf}", prenom="Sans")
        db.session.add_all([adherent, temoin])
        db.session.flush()
        for p in (adherent, temoin):
            p.created_at = vieux
            p.updated_at = vieux
        db.session.add(Cotisation(
            annee_scolaire=2025, type_cotisation="adhesion_individuelle",
            participant_id=adherent.id, montant_du=20, date_reference=date.today(),
        ))
        db.session.commit()

        inactifs = {item["participant"].id for item in participants_inactifs(annees=3)}
        assert temoin.id in inactifs, "sans aucune activité -> anonymisable"
        assert adherent.id not in inactifs, "cotisation récente -> fiche préservée"


# --------------------------------------------------------------------------
# 5. Bénévolat : nb_declares respecte le secteur
# --------------------------------------------------------------------------

def test_benevolat_nb_declares_par_secteur(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.benevolat import stats_annee

        suf = _suffixe_alpha()
        secteur = f"Ben{suf}"
        a = Participant(nom=f"Ben{suf}", prenom="Ici", est_benevole=True, created_secteur=secteur)
        b = Participant(nom=f"Ben{suf}bis", prenom="Ailleurs", est_benevole=True,
                        created_secteur=f"Autre{suf}")
        db.session.add_all([a, b])
        db.session.commit()

        st = stats_annee(2025, secteur=secteur)
        assert st["nb_declares"] == 1, "seul le bénévole déclaré du secteur doit compter"


# --------------------------------------------------------------------------
# 6. Doublons : initiale accentuée retrouvée
# --------------------------------------------------------------------------

def test_doublons_initiale_accentuee(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.doublons import candidats_doublons

        suf = _suffixe_alpha()
        stored = Participant(nom=f"Éric{suf}", prenom="Jean")
        db.session.add(stored)
        db.session.commit()

        trouves = candidats_doublons(f"Eric{suf}", "Jean")
        assert any(c.id == stored.id for c in trouves), (
            "une recherche sans accent doit retrouver la fiche à initiale accentuée"
        )
