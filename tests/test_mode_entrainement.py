"""Mode entraînement : de fausses données pour se former, puis le réel.

Ce que ces tests garantissent :

- l'activation est refusée dès qu'une vraie donnée d'activité existe ;
- sur une base vierge, elle crée le jeu d'exemple et affiche le bandeau ;
- reçus fiscaux et factures ne s'émettent pas (aucun numéro consommé) ;
- quitter efface tout ce qui a été créé pendant l'entraînement, y compris
  les saisies de l'équipe, et garde comptes, réglages et lieux ;
- pas d'effacement sans confirmation tapée, ni sans sauvegarde.

Les cycles complets tournent sur une base dédiée, vierge comme une
installation neuve : la base de test commune contient déjà des habitants.
"""
import datetime as dt
import uuid

import pytest

MOT_DE_PASSE = "motdepasse-tests"


@pytest.fixture(scope="module")
def base_vierge():
    """Une installation neuve, avec un compte direction, sans aucune activité."""
    import conftest

    nom = f"entrainement_{uuid.uuid4().hex[:6]}.db"
    app = conftest._create_app(nom)
    with app.app_context():
        from app.extensions import db
        from app.models import Role, User

        user = User(email="direction@exemple.org", nom="Direction")
        user.set_password(MOT_DE_PASSE)
        user.roles.append(Role.query.filter_by(code="direction").one())
        db.session.add(user)
        db.session.commit()
    yield app
    if conftest._SUR_POSTGRES:
        with app.app_context():
            from app.extensions import db

            db.session.remove()
            db.engine.dispose()
        conftest._supprimer_base(nom.removesuffix(".db"))


@pytest.fixture
def direction(base_vierge):
    client = base_vierge.test_client()
    assert client.post("/", data={"email": "direction@exemple.org", "password": MOT_DE_PASSE}).status_code == 302
    return client


def _compter(app):
    from app.models import AtelierActivite, Cotisation, Encaissement, Participant, PresenceActivite, SessionActivite

    with app.app_context():
        return {m.__tablename__: m.query.count() for m in
                (Participant, AtelierActivite, SessionActivite, PresenceActivite, Cotisation, Encaissement)}


def test_refuse_sur_une_base_qui_a_de_vraies_donnees(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    from app.services import mode_entrainement

    with app.app_context():
        db.session.add(Participant(nom=f"Reel{uuid.uuid4().hex[:6]}", prenom="Vraie"))
        db.session.commit()
        with pytest.raises(mode_entrainement.EntrainementImpossible, match="des habitants"):
            mode_entrainement.activer()
        assert mode_entrainement.actif() is False
    page = admin_client.get("/admin/entrainement").get_data(as_text=True)
    assert "Indisponible sur cette base" in page


def test_cycle_complet_entrainement_puis_reel(base_vierge, direction, monkeypatch):
    from app.extensions import db
    from app.models import Participant, Quartier, User
    from app.services import financial_sequence, mode_entrainement
    from app.services.sauvegarde import dossier_sauvegardes

    app = base_vierge
    assert set(_compter(app).values()) == {0}
    page = direction.get("/admin/entrainement").get_data(as_text=True)
    assert "Activer avec des données d" in page

    # Activation : données d'exemple et bandeau.
    r = direction.post("/admin/entrainement", data={"action": "activer"}, follow_redirects=True)
    assert "Mode entraînement activé" in r.get_data(as_text=True)
    comptes = _compter(app)
    assert comptes["participant"] == 20 and comptes["atelier_activite"] == 3
    assert comptes["session_activite"] == 18 and comptes["presence_activite"] > 0
    assert comptes["cotisation"] == 8 and comptes["encaissement"] == 6
    assert "MODE ENTRAÎNEMENT — FAUSSES DONNÉES" in direction.get("/dashboard").get_data(as_text=True)

    # Reçus fiscaux : refusés, sans consommer de numéro ni compter comme une panne.
    with app.app_context():
        avant = financial_sequence.plus_haut_emis(f"don:{dt.date.today().year}")
    r = direction.post("/dons/nouveau", data={"donateur_nom": "Essai", "montant": "50"},
                       headers={"Referer": "http://localhost/dons"}, follow_redirects=True)
    assert "les reçus fiscaux et les factures ne s" in r.get_data(as_text=True)
    with app.app_context():
        from app.models import Don

        assert Don.query.count() == 0
        assert financial_sequence.plus_haut_emis(f"don:{dt.date.today().year}") == avant
        with pytest.raises(mode_entrainement.EmissionEnEntrainement):
            financial_sequence.next_number("facture:2099", [])

    # Pendant l'entraînement, l'équipe saisit ses essais… et règle ses vrais lieux.
    with app.app_context():
        db.session.add(Participant(nom="Essai", prenom="Saisi pendant l'entraînement"))
        db.session.add(Quartier(ville="Beauvais", nom="Quartier réglé pendant l'entraînement"))
        nouveau_compte = User(email="animatrice@exemple.org", nom="Animatrice")
        nouveau_compte.set_password(MOT_DE_PASSE)
        db.session.add(nouveau_compte)
        db.session.commit()
        lots_avant = {f.name for f in dossier_sauvegardes().glob("*.sha256")}

    # Sans la confirmation tapée, rien ne bouge.
    r = direction.post("/admin/entrainement", data={"action": "quitter", "confirmation": "oui"},
                       follow_redirects=True)
    assert "tapez EFFACER" in r.get_data(as_text=True)
    assert _compter(app)["participant"] == 21

    r = direction.post("/admin/entrainement", data={"action": "quitter", "confirmation": "effacer"},
                       follow_redirects=True)
    assert "Mode réel" in r.get_data(as_text=True)
    assert set(_compter(app).values()) == {0}
    with app.app_context():
        assert mode_entrainement.actif() is False
        assert mode_entrainement.donnees_reelles() == []
        assert Quartier.query.filter_by(nom="Quartier réglé pendant l'entraînement").count() == 1
        assert User.query.filter_by(email="animatrice@exemple.org").count() == 1
        # La sauvegarde d'avant effacement existe.
        assert {f.name for f in dossier_sauvegardes().glob("*.sha256")} - lots_avant
    assert "MODE ENTRAÎNEMENT" not in direction.get("/dashboard").get_data(as_text=True)

    # Et l'on peut s'entraîner de nouveau, puisque la base est redevenue vierge.
    with app.app_context():
        mode_entrainement.activer()
        assert mode_entrainement.actif() is True
        mode_entrainement.quitter()


def test_pas_d_effacement_sans_sauvegarde(base_vierge, direction, monkeypatch):
    from app.services import mode_entrainement, sauvegarde

    with base_vierge.app_context():
        mode_entrainement.activer()
    monkeypatch.setattr(sauvegarde, "creer_sauvegarde", lambda: (_ for _ in ()).throw(RuntimeError("disque plein")))
    r = direction.post("/admin/entrainement", data={"action": "quitter", "confirmation": "EFFACER"},
                       follow_redirects=True)
    assert "disque plein" in r.get_data(as_text=True) and "rien n" in r.get_data(as_text=True)
    assert _compter(base_vierge)["participant"] == 20
    monkeypatch.undo()
    with base_vierge.app_context():
        mode_entrainement.quitter()
        assert mode_entrainement.actif() is False


def test_le_jeu_d_exemple_ne_cree_ni_quartier_ni_salle(base_vierge):
    """Ce qui est gardé en quittant ne doit pas contenir de faux."""
    from app.models import Espace, Quartier, Site, TarifSalle
    from app.services import mode_entrainement

    with base_vierge.app_context():
        avant = [m.query.count() for m in (Quartier, Site, Espace, TarifSalle)]
        mode_entrainement.activer()
        assert [m.query.count() for m in (Quartier, Site, Espace, TarifSalle)] == avant
        mode_entrainement.quitter()


def test_tables_gardees_existent_toutes(app):
    """Une table renommée ne doit pas sortir en silence de la liste des tables gardées."""
    from app.extensions import db
    from app.services.mode_entrainement import TABLES_GARDEES

    with app.app_context():
        assert TABLES_GARDEES <= set(db.metadata.tables)


def test_reserve_aux_administrateurs(client):
    assert client.get("/admin/entrainement").status_code in (302, 401, 403)
    assert client.post("/admin/entrainement", data={"action": "activer"}).status_code in (302, 401, 403)


def test_l_assistant_de_premier_demarrage_peut_lancer_l_entrainement():
    import conftest

    nom = f"assistant_{uuid.uuid4().hex[:6]}.db"
    app = conftest._create_app(nom)
    try:
        client = app.test_client()
        r = client.post("/setup/", data={
            "app_name": "Centre Test", "organization_name": "Centre Test",
            "admin_name": "Direction", "admin_email": "premiere@exemple.org", "admin_password": MOT_DE_PASSE,
            "mode_entrainement": "1",
        })
        assert r.status_code == 302
        from app.services import mode_entrainement

        with app.app_context():
            assert mode_entrainement.actif() is True
        assert _compter(app)["participant"] == 20
    finally:
        if conftest._SUR_POSTGRES:
            with app.app_context():
                from app.extensions import db

                db.session.remove()
                db.engine.dispose()
            conftest._supprimer_base(nom.removesuffix(".db"))


def test_un_effacement_interrompu_n_efface_rien(base_vierge, monkeypatch):
    """Tout ou rien : une erreur au milieu annule l'effacement entier."""
    from app.extensions import db
    from app.services import mode_entrainement

    with base_vierge.app_context():
        mode_entrainement.activer()
    vrai_effacer = mode_entrainement._effacer

    def effacer_puis_tomber(photo, depuis):
        vrai_effacer(photo, depuis)
        raise RuntimeError("panne au milieu")

    monkeypatch.setattr(mode_entrainement, "_effacer", effacer_puis_tomber)
    with base_vierge.app_context():
        with pytest.raises(RuntimeError):
            mode_entrainement.quitter()
        db.session.remove()
    assert _compter(base_vierge)["participant"] == 20
    with base_vierge.app_context():
        mode_entrainement.oublier()
        assert mode_entrainement.actif() is True
    monkeypatch.undo()
    with base_vierge.app_context():
        mode_entrainement.quitter()
