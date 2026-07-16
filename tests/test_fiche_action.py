"""Tests de la fiche action au format imposé : schéma, génération .docx, web."""
import io
import uuid

import pytest


def test_schema_couvre_les_sections_imposees():
    from app.projets.fiche_action import SECTIONS, SECTIONS_PAR_CLE
    # 16 sections imposées
    assert len(SECTIONS) == 16
    for cle in ["objectif_general", "porte_entree", "parcours", "odd", "indicateurs_quali"]:
        assert cle in SECTIONS_PAR_CLE
    # numérotation 1..16 continue
    assert [s.numero for s in SECTIONS] == list(range(1, 17))


def test_serialisation_aller_retour():
    from app.projets.fiche_action import charger, vers_json

    class FauxForm(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    form = FauxForm({
        "entete_axe": "Transitions",
        "entete_public_principal": "Habitants",
        "section_objectif_general": "Aider à l'autonomie",
        "section_parcours": "Il vient | il a un besoin | accueillir",
    })
    from app.projets.fiche_action import depuis_formulaire
    data = depuis_formulaire(form)
    rejoue = charger(vers_json(data))
    assert rejoue["entete"]["axe"] == "Transitions"
    assert rejoue["sections"]["objectif_general"] == "Aider à l'autonomie"


def test_indicateurs_quantitatifs_depuis_stats():
    from app.projets.fiche_action import indicateurs_quantitatifs
    stats = {"kpi": {"sessions": 12, "presences": 80, "uniques": 25, "hours_people": 160},
             "demo": {"age_avg": 41.5, "qpv": {"qpv": 18}}}
    lignes = dict(indicateurs_quantitatifs(stats))
    assert lignes["Nombre de séances réalisées"] == "12"
    assert lignes["Participants uniques"] == "25"
    assert lignes["Dont quartier prioritaire (QPV)"] == "18"


def test_generation_docx_contient_sections_et_chiffres(app):
    from app.projets.fiche_action import charger, generer_docx

    class P: nom = "Projet Démo"; secteur = "Numérique"
    class A: titre = "Numérique pour Tous"

    fiche = charger('{"entete":{"axe":"Transitions"},'
                    '"sections":{"objectif_general":"Autonomie numérique",'
                    '"parcours":"Il vient | besoin | accueillir"}}')
    stats = {"kpi": {"sessions": 7, "presences": 50, "uniques": 20, "hours_people": 100},
             "demo": {"age_avg": 40, "qpv": {"qpv": 5}}}
    doc = generer_docx(P(), A(), fiche, stats, 2025)

    texte = "\n".join(p.text for p in doc.paragraphs)
    assert "Fiche action — Numérique pour Tous" in texte
    assert "1. Intitulé et objectif général" in texte
    assert "16. Objectifs de développement durable" in texte
    # Les chiffres réels apparaissent dans un tableau
    cellules = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    assert "Nombre de séances réalisées" in cellules and "7" in cellules
    assert "Participants uniques" in cellules and "20" in cellules


@pytest.fixture()
def action_projet(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Projet, ProjetAction
        suffixe = uuid.uuid4().hex[:6]
        p = Projet(nom=f"Projet {suffixe}", secteur="Familles")
        db.session.add(p)
        db.session.flush()
        a = ProjetAction(projet_id=p.id, titre=f"Action {suffixe}")
        db.session.add(a)
        db.session.commit()
        return {"projet_id": p.id, "action_id": a.id, "suffixe": suffixe}


def test_editeur_affiche_les_16_sections(admin_client, action_projet):
    r = admin_client.get(
        f"/projets/{action_projet['projet_id']}/actions/{action_projet['action_id']}/fiche/editer"
    )
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Fiche action" in page
    assert "16. Objectifs de développement durable" in page


def test_enregistrement_puis_docx(app, admin_client, action_projet):
    pid, aid = action_projet["projet_id"], action_projet["action_id"]
    r = admin_client.post(
        f"/projets/{pid}/actions/{aid}/fiche/editer",
        data={
            "entete_axe": "Transitions numériques",
            "section_objectif_general": "Autonomie des habitants",
            "section_porte_entree": "Je n'arrive pas à faire ma démarche.\nJ'ai peur de me tromper.",
        },
    )
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import ProjetAction
        from app.projets.fiche_action import charger
        a = db.session.get(ProjetAction, aid)
        fiche = charger(a.fiche_json)
        assert fiche["entete"]["axe"] == "Transitions numériques"
        assert "autonomie" in fiche["sections"]["objectif_general"].lower()

    # Le document se génère et contient le contenu saisi
    r = admin_client.get(f"/projets/{pid}/actions/{aid}/fiche.docx")
    assert r.status_code == 200
    assert "wordprocessingml" in r.content_type
    assert len(r.data) > 1000  # un vrai .docx


def test_acces_refuse_autre_secteur(app, action_projet):
    """Un responsable d'un autre secteur ne voit pas la fiche."""
    email = f"resp-{uuid.uuid4().hex[:6]}@example.org"
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User
        u = User(email=email, nom="Resp Num", secteur_assigne="Numérique")
        u.set_password("motdepasse-tests")
        u.roles.append(Role.query.filter_by(code="responsable_secteur").first())
        db.session.add(u)
        db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": "motdepasse-tests"})
    # action_projet est en secteur "Familles"
    r = c.get(f"/projets/{action_projet['projet_id']}/actions/{action_projet['action_id']}/fiche/editer")
    assert r.status_code == 403
