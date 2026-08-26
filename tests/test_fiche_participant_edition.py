"""Enregistrement d'une fiche participant existante.

Symptôme d'origine : « le bouton d'enregistrement ne fonctionne pas en
modification, mais la création marche ». Cause : les formulaires
d'anonymisation, affichés uniquement en édition, étaient imbriqués dans le
formulaire de la fiche — le navigateur refermait donc celui-ci avant le
bouton « Enregistrer les modifications ».
"""
import datetime as dt
import re
import uuid


def _participant(app, **champs):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        p = Participant(**champs)
        db.session.add(p)
        db.session.commit()
        return p.id


def test_le_bouton_enregistrer_appartient_au_formulaire(app, admin_client):
    """Le bouton doit se trouver AVANT la fermeture du formulaire de fiche :
    au-delà, le navigateur ne le rattache plus et le clic ne soumet rien."""
    tag = uuid.uuid4().hex[:6]
    pid = _participant(app, nom=f"DAWA{tag}", prenom="Dorgie", ville="Nogent sur Oise",
                       genre="Homme", type_public="H", date_naissance=dt.date(1985, 3, 30))

    page = admin_client.get(f"/participants/{pid}/edit").get_data(as_text=True)
    assert "Enregistrer les modifications" in page

    ouverture = page.index('data-pending-form="participant"')
    fermeture = page.index("</form>", ouverture)
    bouton = page.index("Enregistrer les modifications")
    assert ouverture < bouton < fermeture, (
        "le bouton d'enregistrement est sorti du formulaire de la fiche"
    )

    # Et aucun formulaire d'anonymisation ne doit vivre à l'intérieur.
    assert "anonymize" not in page[ouverture:fermeture]


def test_modification_effectivement_enregistree(app, admin_client):
    from app.extensions import db
    from app.models import Participant

    tag = uuid.uuid4().hex[:6]
    pid = _participant(app, nom=f"DAWA{tag}", prenom="Dorgie", ville="Nogent sur Oise",
                       genre="Homme", type_public="H", date_naissance=dt.date(1985, 3, 30))

    r = admin_client.post(f"/participants/{pid}/edit", data={
        "nom": f"DAWA{tag}", "prenom": "Dorgie", "ville": "Creil",
        "telephone": "0612345678", "genre": "Homme", "type_public": "H",
    })
    assert r.status_code == 302
    with app.app_context():
        p = db.session.get(Participant, pid)
        assert p.ville == "Creil"
        assert p.telephone == "0612345678"


def test_creation_toujours_fonctionnelle(app, admin_client):
    """Non-régression : le cas qui marchait doit continuer de marcher."""
    from app.models import Participant

    tag = uuid.uuid4().hex[:6]
    r = admin_client.post("/participants/new", data={
        "nom": f"NOUVEAU{tag}", "prenom": "Test", "ville": "Creil",
        "genre": "Femme", "type_public": "H",
    })
    assert r.status_code == 302
    with app.app_context():
        assert Participant.query.filter_by(nom=f"NOUVEAU{tag}").first() is not None
