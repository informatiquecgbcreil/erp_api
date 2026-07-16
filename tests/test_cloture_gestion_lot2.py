"""Gestion Lot 2 — Clôture d'exercice : archivage subventions, verrou budget, reconduction N→N+1."""
import uuid

from flask import url_for


def _sub(app, **kw):
    from app.extensions import db
    from app.models import Subvention
    defaults = dict(nom=f"S{uuid.uuid4().hex[:6]}", secteur="Numérique", annee_exercice=2040,
                    financeur="CAF", statut_cycle="accordee", montant_demande=1000,
                    montant_attribue=800, montant_recu=400)
    defaults.update(kw)
    s = Subvention(**defaults)
    db.session.add(s)
    db.session.commit()
    return s.id


def test_archiver_restaurer_subvention(app, admin_client):
    from app.extensions import db
    from app.models import Subvention, AuditLog

    with app.app_context():
        sid = _sub(app)
        with app.test_request_context():
            url_arch = url_for("main.subvention_archive", subvention_id=sid)
            url_rest = url_for("main.subvention_restore", subvention_id=sid)

    admin_client.post(url_arch, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Subvention, sid).est_archive is True
        assert AuditLog.query.filter_by(action="subvention.archive").count() >= 1

    admin_client.post(url_rest, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Subvention, sid).est_archive is False


def test_reconduction_subventions(app, admin_client):
    from app.extensions import db
    from app.models import Subvention, LigneBudget

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        sid = _sub(app, nom=f"Recond{tag}", annee_exercice=2041, montant_demande=900, montant_attribue=900, montant_recu=500)
        db.session.add(LigneBudget(subvention_id=sid, nature="charge", compte="60",
                                   libelle="Achat", montant_base=300, montant_reel=250))
        db.session.commit()
        with app.test_request_context():
            url = url_for("main.subventions_reconduire")

    admin_client.post(url, data={"annee_source": "2041", "annee_cible": "2042"}, follow_redirects=True)
    with app.app_context():
        copie = Subvention.query.filter_by(nom=f"Recond{tag}", annee_exercice=2042).first()
        assert copie is not None
        assert copie.statut_cycle == "sollicitee"
        assert copie.montant_attribue == 0.0 and copie.montant_recu == 0.0
        assert copie.montant_demande == 900          # demandé conservé
        lignes = LigneBudget.query.filter_by(subvention_id=copie.id).all()
        assert len(lignes) == 1
        assert lignes[0].montant_base == 300 and lignes[0].montant_reel == 0.0


def test_reconduction_dedoublonne(app, admin_client):
    from app.extensions import db
    from app.models import Subvention

    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        _sub(app, nom=f"Dup{tag}", financeur="Région", annee_exercice=2043)
        # déjà présent en année cible
        _sub(app, nom=f"Dup{tag}", financeur="Région", annee_exercice=2044)
        with app.test_request_context():
            url = url_for("main.subventions_reconduire")

    admin_client.post(url, data={"annee_source": "2043", "annee_cible": "2044"}, follow_redirects=True)
    with app.app_context():
        n = Subvention.query.filter_by(nom=f"Dup{tag}", financeur="Région", annee_exercice=2044).count()
        assert n == 1   # pas de doublon créé


def test_budget_archive_verrouille(app, admin_client):
    from app.extensions import db
    from app.models import BudgetPrevisionnel, BudgetPrevisionnelLigne

    with app.app_context():
        b = BudgetPrevisionnel(nom=f"B{uuid.uuid4().hex[:6]}", annee=2045, secteur="Numérique", statut="archive")
        db.session.add(b)
        db.session.commit()
        bid = b.id
        with app.test_request_context():
            url = url_for("previsionnel.detail", budget_id=bid)

    # Tentative d'ajout de ligne sur budget archivé -> refusée
    admin_client.post(url, data={"action": "add_line", "nature": "charge",
                                 "libelle": "Interdit", "montant": "100"}, follow_redirects=True)
    with app.app_context():
        assert BudgetPrevisionnelLigne.query.filter_by(budget_id=bid).count() == 0


def test_budget_duplication(app, admin_client):
    from app.extensions import db
    from app.models import BudgetPrevisionnel, BudgetPrevisionnelLigne

    with app.app_context():
        b = BudgetPrevisionnel(nom=f"Bd{uuid.uuid4().hex[:6]}", annee=2046, secteur="Numérique", statut="valide")
        db.session.add(b)
        db.session.flush()
        db.session.add(BudgetPrevisionnelLigne(budget_id=b.id, nature="charge", compte="60",
                                               libelle="Loyer", montant=1200, ordre=1))
        db.session.commit()
        bid = b.id
        with app.test_request_context():
            url = url_for("previsionnel.duplicate", budget_id=bid)

    admin_client.post(url, data={"annee_cible": "2047"}, follow_redirects=True)
    with app.app_context():
        copie = BudgetPrevisionnel.query.filter_by(annee=2047, secteur="Numérique").filter(
            BudgetPrevisionnel.id != bid).first()
        assert copie is not None
        assert copie.statut == "brouillon"
        assert BudgetPrevisionnelLigne.query.filter_by(budget_id=copie.id).count() == 1
