"""Inscrire depuis une fiche ancienne ne doit pas fabriquer de doublon.

Le module d'inscription est récent ; les fiches participants, non. Une
personne qui vient depuis un an porte une écriture d'avant toute
normalisation : « nogent sur OISE », « Femme ».

Le bulletin, lui, est normalisé à la saisie. D'où le piège : le bulletin
ressort propre (« Nogent-sur-Oise »), la fiche reste sale — et la même
commune existe désormais sous DEUX écritures dans la base. C'est le geste
censé ranger qui vient de fabriquer le doublon.

La règle retenue : une fiche touchée par un bulletin voit son ÉCRITURE
uniformisée. Jamais son sens. Une commune n'est jamais remplacée par une
autre — « Nogent » reste « Nogent », c'est à la fusion de trancher, avec
quelqu'un qui connaît le territoire.
"""
import uuid
from datetime import date


def _suffixe():
    return uuid.uuid4().hex[:6]


def test_la_fiche_ancienne_est_uniformisee(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.inscriptions_annuelles import uniformiser_la_fiche

        p = Participant(nom=f"Ancien{_suffixe()}", prenom="Marie",
                        ville="nogent sur OISE", genre="Femme")
        db.session.add(p)
        db.session.commit()

        touches = uniformiser_la_fiche(p)
        db.session.commit()

        assert p.ville == "Nogent-sur-Oise"
        assert p.genre == "F"
        assert sorted(touches) == ["genre", "ville"]


def test_une_fiche_deja_propre_nest_pas_touchee(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.inscriptions_annuelles import uniformiser_la_fiche

        p = Participant(nom=f"Propre{_suffixe()}", prenom="Ana",
                        ville="Creil", genre="F")
        db.session.add(p)
        db.session.commit()
        assert uniformiser_la_fiche(p) == []


def test_une_troncature_nest_jamais_completee(app):
    """« Nogent » peut être Nogent-sur-Oise… ou pas. On ne devine pas."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.inscriptions_annuelles import uniformiser_la_fiche

        p = Participant(nom=f"Tronc{_suffixe()}", prenom="Bo", ville="nogent")
        db.session.add(p)
        db.session.commit()

        uniformiser_la_fiche(p)
        assert p.ville == "Nogent", "l'écriture est corrigée, la commune jamais devinée"


def test_une_valeur_illisible_reste_en_place(app):
    """On ne vide pas une fiche au passage : ce qu'on ne sait pas lire, on
    le laisse. Une case vidée en douce est pire qu'une case bizarre."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.inscriptions_annuelles import uniformiser_la_fiche

        p = Participant(nom=f"Flou{_suffixe()}", prenom="Zed", genre="à demander")
        db.session.add(p)
        db.session.commit()

        uniformiser_la_fiche(p)
        assert p.genre == "à demander"


def test_le_parcours_complet_ne_laisse_quune_ecriture(admin_client, app):
    """Le scénario réel : bouton « Inscrire pour… » depuis une fiche de
    l'an dernier, puis enregistrement."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        p = Participant(nom=f"Parcours{suf}", prenom="Marie",
                        ville="nogent sur OISE", genre="Femme",
                        date_naissance=date(1979, 3, 14), created_secteur="Adultes")
        db.session.add(p)
        db.session.commit()
        pid = p.id

    # Le formulaire s'ouvre prérempli, et reconnaît l'ancien libellé de genre.
    page = admin_client.get(
        f"/inscriptions-annuelles/nouvelle?participant_id={pid}&annee=2053"
    ).get_data(as_text=True)
    assert "Prérempli depuis la fiche" in page
    assert 'value="F"' in page and "selected" in page

    admin_client.post("/inscriptions-annuelles/nouvelle?annee=2053", data={
        "annee": 2053, "participant_id": pid, "nom": f"Parcours{suf}",
        "prenom": "Marie", "ville": "nogent sur OISE", "genre": "F",
    }, follow_redirects=True)

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant

        bulletin = InscriptionAnnuelle.query.filter_by(
            annee_scolaire=2053, participant_id=pid).one()
        fiche = db.session.get(Participant, pid)

        # LE point du test : une seule écriture de la commune dans la base.
        assert bulletin.ville == fiche.ville == "Nogent-sur-Oise"
        assert bulletin.genre == fiche.genre == "F"
        # Et le rattachement s'est fait tout seul, sans créer de doublon.
        assert Participant.query.filter_by(nom=f"Parcours{suf}").count() == 1


def test_le_quartier_de_la_fiche_est_preserve(admin_client, app):
    """Le bulletin ne porte pas de quartier : il ne doit pas l'effacer."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Quartier

        q = Quartier(ville="Creil", nom=f"Rouher{suf}", qpv="Hauts de Creil")
        db.session.add(q)
        db.session.flush()
        p = Participant(nom=f"Quartier{suf}", prenom="Sam", ville="Creil", quartier_id=q.id)
        db.session.add(p)
        db.session.commit()
        pid, qid = p.id, q.id

    admin_client.post("/inscriptions-annuelles/nouvelle?annee=2054", data={
        "annee": 2054, "participant_id": pid,
        "nom": f"Quartier{suf}", "prenom": "Sam", "ville": "Creil",
    }, follow_redirects=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        assert db.session.get(Participant, pid).quartier_id == qid
