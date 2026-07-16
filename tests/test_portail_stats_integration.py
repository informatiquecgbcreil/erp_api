"""Le portail se fond dans les stats pédagogiques (niveau effectif = max manuel/portail)."""
import datetime as dt
import uuid


def _competence():
    from app.extensions import db
    from app.models import Competence, Referentiel

    suf = uuid.uuid4().hex[:6]
    ref = Referentiel(nom=f"Ref {suf}")
    db.session.add(ref)
    db.session.flush()
    code = f"P-{suf}"
    c = Competence(referentiel_id=ref.id, code=code, nom="Comp")
    db.session.add(c)
    db.session.commit()
    return c.id, code


def test_niveaux_portail_par_paire(app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap
    from app.pedagogie.services import niveaux_portail_par_paire

    with app.app_context():
        cid, _ = _competence()
        p = Participant(nom="N", prenom="P", type_public="H")
        db.session.add(p)
        db.session.flush()
        pid = p.id
        db.session.add(PortailCompetenceMap(activity="ExA", competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id="x1", external_id=str(pid), participant_id=pid,
                                      activity="ExA", pct=90, score=90, max_score=100,
                                      finished_at=dt.datetime(2026, 6, 20, 10, 0)))
        db.session.commit()

        res = niveaux_portail_par_paire({cid})
        assert res.get((pid, cid)) == 3  # 1/1 réussi → N3


def test_participant_timeline_enrichi(app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap
    from app.pedagogie.services import participant_timeline

    with app.app_context():
        cid, _ = _competence()
        p = Participant(nom="Tl", prenom="P", type_public="H")
        db.session.add(p)
        db.session.flush()
        pid = p.id
        db.session.add(PortailCompetenceMap(activity="ExT", competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id="t1", external_id=str(pid), participant_id=pid,
                                      activity="ExT", pct=85, score=85, max_score=100,
                                      finished_at=dt.datetime(2026, 6, 20, 10, 0)))
        db.session.commit()

        _participant, _events, current_levels = participant_timeline(pid)
        assert current_levels.get(cid) == 3  # enrichi par le portail, sans évaluation manuelle


def test_bilan_rows_inclut_portail(app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap
    from app.statsimpact.pedagogie import _build_bilan_rows

    with app.app_context():
        cid, code = _competence()
        p = Participant(nom="Bil", prenom="P", type_public="H")
        db.session.add(p)
        db.session.flush()
        db.session.add(PortailCompetenceMap(activity="ExB", competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id="b1", external_id=str(p.id), participant_id=p.id,
                                      activity="ExB", pct=95, score=95, max_score=100,
                                      finished_at=dt.datetime(2026, 6, 20, 10, 0)))
        db.session.commit()

        rows = _build_bilan_rows(p)
        assert any(code in r["competence"] for r in rows)
        assert any(r["atelier"] == "Portail" for r in rows)


def test_bilan_financeur_inclut_portail(app):
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap
    from app.bilans.services import BilansScope, _compute_bilans_lourds_core

    with app.app_context():
        cid, _ = _competence()
        p = Participant(nom="BilFin", prenom="P", type_public="H", created_secteur="Numérique")
        db.session.add(p)
        db.session.flush()
        act = f"ExF-{uuid.uuid4().hex[:6]}"
        db.session.add(PortailCompetenceMap(activity=act, competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id=f"bf-{uuid.uuid4().hex[:6]}", external_id=str(p.id),
                                      participant_id=p.id, activity=act, pct=92, score=92, max_score=100,
                                      finished_at=dt.datetime(2026, 5, 10, 10, 0)))
        db.session.commit()

        res = _compute_bilans_lourds_core(2026, BilansScope(secteurs=None))
        ev = res["evaluations"]
        assert ev["dont_portail"]["items"] >= 1
        assert ev["dont_portail"]["participants"] >= 1
        assert ev["total"] >= 1
        assert ev["nb_competences_uniques"] >= 1


def test_bilan_financeur_respecte_le_secteur(app):
    """Le filtre secteur exclut bien un participant hors périmètre."""
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap
    from app.bilans.services import BilansScope, _compute_bilans_lourds_core

    with app.app_context():
        cid, _ = _competence()
        secteur = f"SECT-{uuid.uuid4().hex[:8]}"   # secteur unique, utilisé nulle part ailleurs
        autre = f"AUTRE-{uuid.uuid4().hex[:8]}"
        p = Participant(nom="Sect", prenom="P", type_public="H", created_secteur=secteur)
        db.session.add(p)
        db.session.flush()
        act = f"ExS-{uuid.uuid4().hex[:6]}"
        db.session.add(PortailCompetenceMap(activity=act, competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id=f"bs-{uuid.uuid4().hex[:6]}", external_id=str(p.id),
                                      participant_id=p.id, activity=act, pct=92, score=92, max_score=100,
                                      finished_at=dt.datetime(2026, 5, 10, 10, 0)))
        db.session.commit()

        # périmètre = le secteur du participant -> compté
        dans = _compute_bilans_lourds_core(2026, BilansScope(secteurs=[secteur]))
        assert dans["evaluations"]["dont_portail"]["items"] == 1

        # périmètre = un autre secteur (aucun participant) -> rien
        hors = _compute_bilans_lourds_core(2026, BilansScope(secteurs=[autre]))
        assert hors["evaluations"]["dont_portail"]["items"] == 0


def test_bilan_lourds_affiche_dont_portail_sans_bug(admin_client, app):
    """La page bilan lourd rend bien le nombre d'items portail (pas la méthode dict)."""
    from app.extensions import db
    from app.models import Participant, PortailAttempt, PortailCompetenceMap

    with app.app_context():
        cid, _ = _competence()
        p = Participant(nom="BilRender", prenom="P", type_public="H", created_secteur="Numérique")
        db.session.add(p)
        db.session.flush()
        act = f"ExR-{uuid.uuid4().hex[:6]}"
        db.session.add(PortailCompetenceMap(activity=act, competence_id=cid, seuil_pct=80, actif=True))
        db.session.add(PortailAttempt(attempt_id=f"br-{uuid.uuid4().hex[:6]}", external_id=str(p.id),
                                      participant_id=p.id, activity=act, pct=95, score=95, max_score=100,
                                      finished_at=dt.datetime(2026, 5, 10, 10, 0)))
        db.session.commit()

    page = admin_client.get("/bilans/lourds?year=2026").get_data(as_text=True)
    assert "built-in method" not in page   # le bug (rendu de dict.items) ne doit plus apparaître
    assert "via le portail" in page
