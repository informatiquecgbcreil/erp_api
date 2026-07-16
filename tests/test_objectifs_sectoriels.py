"""Tests des objectifs sectoriels : référentiel par secteur, rattachement, consolidation."""
import uuid

import pytest


@pytest.fixture()
def secteur_os(app):
    """Crée 2 objectifs pour le secteur Familles + un projet/action dans ce secteur."""
    with app.app_context():
        from app.extensions import db
        from app.models import ObjectifSectoriel, Projet, ProjetAction
        suf = uuid.uuid4().hex[:6]
        os1 = ObjectifSectoriel(secteur="Familles", code="OS1", libelle=f"Comprendre {suf}", ordre=1)
        os2 = ObjectifSectoriel(secteur="Familles", code="OS2", libelle=f"Coopérer {suf}", ordre=2)
        # objectif d'un AUTRE secteur, ne doit pas fuiter
        autre = ObjectifSectoriel(secteur="Numérique", code="OS1", libelle="Autre", ordre=1)
        db.session.add_all([os1, os2, autre])
        p = Projet(nom=f"Projet {suf}", secteur="Familles")
        db.session.add(p)
        db.session.flush()
        a = ProjetAction(projet_id=p.id, titre=f"Action {suf}")
        db.session.add(a)
        db.session.commit()
        return {"os1": os1.id, "os2": os2.id, "autre": autre.id,
                "projet_id": p.id, "action_id": a.id, "suf": suf}


def test_objectifs_cloisonnes_par_secteur(app, secteur_os):
    with app.app_context():
        from app.projets.objectifs import objectifs_du_secteur
        codes = [o.id for o in objectifs_du_secteur("Familles")]
        assert secteur_os["os1"] in codes and secteur_os["os2"] in codes
        assert secteur_os["autre"] not in codes, "un objectif d'un autre secteur ne doit pas remonter"


def test_referentiel_crud_admin(admin_client):
    suf = uuid.uuid4().hex[:6]
    # Ajout
    r = admin_client.post("/projets/objectifs-sectoriels",
                          data={"action": "ajouter", "secteur": "Santé Transition",
                                "code": "OSX", "libelle": f"Prévention {suf}", "ordre": "1"})
    assert r.status_code == 302
    page = admin_client.get("/projets/objectifs-sectoriels?secteur=Santé Transition").get_data(as_text=True)
    assert f"Prévention {suf}" in page


def test_rattachement_action_et_generation(app, admin_client, secteur_os):
    pid, aid = secteur_os["projet_id"], secteur_os["action_id"]
    # On rattache l'action à OS1 avec une contribution, via l'éditeur de fiche
    r = admin_client.post(
        f"/projets/{pid}/actions/{aid}/fiche/editer",
        data={
            "section_objectif_general": "Objectif test",
            f"os_{secteur_os['os1']}": "1",
            f"contrib_{secteur_os['os1']}": "Accompagner les familles",
        },
    )
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import ActionObjectifSectoriel
        liens = ActionObjectifSectoriel.query.filter_by(action_id=aid).all()
        assert len(liens) == 1
        assert liens[0].objectif_id == secteur_os["os1"]
        assert "familles" in (liens[0].contribution or "").lower()

    # Le document généré contient la contribution structurée en section 2
    r = admin_client.get(f"/projets/{pid}/actions/{aid}/fiche.docx")
    assert r.status_code == 200
    from io import BytesIO
    from docx import Document
    doc = Document(BytesIO(r.data))
    cellules = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    assert any("Accompagner les familles" in c for c in cellules)


def test_decochage_supprime_le_lien(app, admin_client, secteur_os):
    pid, aid = secteur_os["projet_id"], secteur_os["action_id"]
    # 1) on rattache
    admin_client.post(f"/projets/{pid}/actions/{aid}/fiche/editer",
                      data={f"os_{secteur_os['os1']}": "1", f"contrib_{secteur_os['os1']}": "X"})
    # 2) on re-soumet sans cocher -> le lien doit disparaître
    admin_client.post(f"/projets/{pid}/actions/{aid}/fiche/editer", data={})
    with app.app_context():
        from app.models import ActionObjectifSectoriel
        assert ActionObjectifSectoriel.query.filter_by(action_id=aid).count() == 0


def test_consolidation_agrege_les_actions(app, admin_client, secteur_os):
    pid, aid = secteur_os["projet_id"], secteur_os["action_id"]
    admin_client.post(f"/projets/{pid}/actions/{aid}/fiche/editer",
                      data={f"os_{secteur_os['os1']}": "1", f"contrib_{secteur_os['os1']}": "Contribution visible"})

    r = admin_client.get("/projets/objectifs-sectoriels/synthese?secteur=Familles")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Contribution visible" in page
    assert secteur_os["suf"] in page  # le nom de l'action apparaît


def test_responsable_secteur_ne_gere_que_le_sien(app):
    email = f"resp-{uuid.uuid4().hex[:6]}@example.org"
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User(email=email, nom="Resp Familles", secteur_assigne="Familles")
        u.set_password("motdepasse-tests")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": "motdepasse-tests"})

    # Peut accéder à la page (sur son secteur)
    assert c.get("/projets/objectifs-sectoriels").status_code == 200
    # Ne peut pas créer un objectif pour un AUTRE secteur
    r = c.post("/projets/objectifs-sectoriels",
               data={"action": "ajouter", "secteur": "Numérique", "code": "OSZ", "libelle": "Pirate"})
    assert r.status_code == 403


def test_section2_texte_libre_preserve_quand_masquee(app, admin_client, secteur_os):
    """P1 — Régression : le texte libre de la section 2 ne doit pas être perdu
    au ré-enregistrement lorsque le secteur a des objectifs (champ masqué)."""
    pid, aid = secteur_os["projet_id"], secteur_os["action_id"]

    # 1) On force d'abord un contenu libre en section 2 directement en base
    with app.app_context():
        from app.extensions import db
        from app.models import ProjetAction
        from app.projets.fiche_action import charger, vers_json
        a = db.session.get(ProjetAction, aid)
        fiche = charger(a.fiche_json)
        fiche.setdefault("sections", {})["objectifs_specifiques"] = "OS historique en texte libre"
        a.fiche_json = vers_json(fiche)
        db.session.commit()

    # 2) Ré-enregistrement via l'éditeur SANS le champ section_objectifs_specifiques
    #    (masqué car le secteur a des objectifs) : il doit être préservé.
    r = admin_client.post(
        f"/projets/{pid}/actions/{aid}/fiche/editer",
        data={"section_objectif_general": "Mise à jour"},
    )
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import ProjetAction
        from app.projets.fiche_action import charger
        a = db.session.get(ProjetAction, aid)
        fiche = charger(a.fiche_json)
        assert fiche["sections"]["objectifs_specifiques"] == "OS historique en texte libre"
        assert fiche["sections"]["objectif_general"] == "Mise à jour"


def test_created_at_a_un_server_default(app):
    """P2 — Régression : une insertion SQL directe (sans created_at) doit réussir
    grâce au server_default de la migration."""
    from sqlalchemy import text
    with app.app_context():
        from app.extensions import db
        db.session.execute(text(
            "INSERT INTO objectif_sectoriel (secteur, code, libelle) "
            "VALUES ('Familles', 'OSX', 'Insertion directe')"
        ))
        db.session.commit()
        row = db.session.execute(text(
            "SELECT created_at FROM objectif_sectoriel WHERE code='OSX' AND libelle='Insertion directe'"
        )).first()
        assert row is not None and row[0] is not None, "created_at doit être renseigné par le server_default"
