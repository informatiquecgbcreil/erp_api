"""Lot 4 Projet — Cycle de vie : archivage, reconduction, jalons, export liste."""
import datetime as dt
import json
import uuid

import pytest
from flask import url_for


@pytest.fixture()
def projet_complet(app):
    """Projet avec 1 atelier lié, 1 indicateur, 1 action."""
    with app.app_context():
        from app.extensions import db
        from app.models import (Projet, AtelierActivite, ProjetAtelier,
                                 ProjetIndicateur, ProjetAction)
        suf = uuid.uuid4().hex[:6]
        p = Projet(nom=f"Proj {suf}", secteur="Numérique", description="desc")
        db.session.add(p)
        db.session.flush()
        at = AtelierActivite(nom=f"At {suf}", secteur="Numérique", type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        db.session.add(ProjetAtelier(projet_id=p.id, atelier_id=at.id))
        db.session.add(ProjetIndicateur(projet_id=p.id, code=f"manual_number_{suf}", label="Ind",
                                        is_active=True, params_json=json.dumps({"source": "manual", "value_type": "number"})))
        db.session.add(ProjetAction(projet_id=p.id, titre=f"Action {suf}"))
        db.session.commit()
        return {"projet_id": p.id, "suf": suf}


def test_archiver_et_restaurer(app, admin_client, projet_complet):
    from app.extensions import db
    from app.models import Projet, AuditLog

    pid = projet_complet["projet_id"]
    with app.app_context():
        with app.test_request_context():
            url_arch = url_for("projets.projets_archive", projet_id=pid)
            url_rest = url_for("projets.projets_restore", projet_id=pid)
            url_list = url_for("projets.projets_list")

    admin_client.post(url_arch, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Projet, pid).is_archive is True
        assert AuditLog.query.filter_by(action="projet.archive").count() >= 1

    # n'apparaît plus dans la liste active
    body = admin_client.get(url_list).get_data(as_text=True)
    assert f"Proj {projet_complet['suf']}" not in body

    admin_client.post(url_rest, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Projet, pid).is_archive is False


def test_reconduction_copie_structure(app, admin_client, projet_complet):
    from app.extensions import db
    from app.models import Projet, ProjetIndicateur, ProjetAtelier, ProjetAction

    pid = projet_complet["projet_id"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projets_duplicate", projet_id=pid)

    admin_client.post(url, follow_redirects=True)
    with app.app_context():
        copie = Projet.query.filter(Projet.nom.like("%(reconduction)%")).first()
        assert copie is not None and copie.id != pid
        assert ProjetIndicateur.query.filter_by(projet_id=copie.id).count() == 1
        assert ProjetAtelier.query.filter_by(projet_id=copie.id).count() == 1
        assert ProjetAction.query.filter_by(projet_id=copie.id).count() == 1


def test_jalons_crud_et_retard(app, admin_client, projet_complet):
    from app.extensions import db
    from app.models import ProjetJalon

    pid = projet_complet["projet_id"]
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projet_jalons", projet_id=pid)

    # Ajout d'un jalon échu (en retard)
    admin_client.post(url, data={"action": "add", "libelle": "Dépôt dossier",
                                 "date_echeance": "2020-01-01", "ordre": "1"}, follow_redirects=True)
    with app.app_context():
        jal = ProjetJalon.query.filter_by(projet_id=pid).first()
        assert jal is not None and jal.statut == "a_faire"
        jid = jal.id

    page = admin_client.get(url).get_data(as_text=True)
    assert "en retard" in page.lower()

    # Marquer fait
    admin_client.post(url, data={"action": "toggle", "jalon_id": jid}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(ProjetJalon, jid).statut == "fait"

    # Supprimer
    admin_client.post(url, data={"action": "delete", "jalon_id": jid}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(ProjetJalon, jid) is None


def test_export_liste_projets_xlsx(app, admin_client, projet_complet):
    with app.app_context():
        with app.test_request_context():
            url = url_for("projets.projets_export_xlsx")
    r = admin_client.get(url)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    assert len(r.data) > 0
