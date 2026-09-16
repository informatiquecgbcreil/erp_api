"""La recherche doit savoir sur quelle base elle tourne.

Panne constatée en production (PostgreSQL) : la recherche ne renvoyait
plus RIEN, sur aucun type d'objet.

    function sans_accent(character varying) does not exist

La cause tenait en une ligne, et elle dormait depuis longtemps :

    dialect_name = ((db.session.bind.dialect.name if db.session.bind else "") or "").lower()

``db.session.bind`` vaut None avec Flask-SQLAlchemy 3 tant qu'aucun bind
explicite n'est posé. Le nom du dialecte retombait donc sur la chaîne
vide, et la recherche partait systématiquement dans la branche « pas
PostgreSQL ».

Ce défaut est passé inaperçu des années durant parce que cette branche
utilisait ``lower()``, qui existe dans toutes les bases. Il est devenu
fatal le jour où la branche est devenue propre à SQLite, avec la fonction
``sans_accent`` enregistrée sur la connexion SQLite — inexistante
ailleurs.

Et la suite de tests ne pouvait pas l'attraper : elle tourne sur SQLite,
où la « mauvaise » branche se trouvait être la bonne.
"""


def test_le_dialecte_est_reellement_identifie(app):
    """LE test qui aurait évité la panne : avant correction, la fonction
    rendait une chaîne vide au lieu du nom de la base."""
    with app.app_context():
        from app.main.recherche import _nom_du_dialecte

        nom = _nom_du_dialecte()

    assert nom, "le dialecte ne doit jamais être vide : c'est ce qui envoyait la recherche dans la mauvaise branche"
    assert nom in {"sqlite", "postgresql", "mysql", "mariadb"}, nom


def test_le_dialecte_ne_depend_pas_du_bind_de_session(app):
    """« db.session.bind » est None ici : la détection ne doit pas s'y fier."""
    with app.app_context():
        from app.extensions import db
        from app.main.recherche import _nom_du_dialecte

        assert db.session.bind is None, (
            "si ce bind devient non-None, la ligne d'origine n'était plus le "
            "problème — vérifier le diagnostic avant de toucher au test"
        )
        assert _nom_du_dialecte() == db.engine.dialect.name


def test_une_base_inconnue_retombe_sur_du_sql_universel(admin_client, monkeypatch):
    """Un dialecte qu'on ne connaît pas ne doit pas faire appel à une
    fonction maison : une recherche moins fine vaut mieux qu'une erreur.

    C'est exactement le scénario de la panne : la recherche croyait tourner
    sur une base inconnue et appelait quand même sans_accent().
    """
    import uuid

    import app.main.recherche as recherche

    monkeypatch.setattr(recherche, "_nom_du_dialecte", lambda: "unebasequonneconnaitpas")

    suf = uuid.uuid4().hex[:6]
    r = admin_client.get(f"/api/global-search?q=Inconnu{suf}")

    # Le point du test : la requête a été CONSTRUITE et EXÉCUTÉE sans erreur.
    assert r.status_code == 200
    assert r.get_json().get("results") == []


def test_la_recherche_repond_sur_la_base_du_projet(admin_client, app):
    """Bout en bout, sur le dialecte réellement utilisé par les tests."""
    import uuid

    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        db.session.add(Participant(nom=f"Dialecte{suf}", prenom="Test", ville="Creil"))
        db.session.commit()

    r = admin_client.get(f"/api/global-search?q=Dialecte{suf}")
    assert r.status_code == 200
    resultats = r.get_json().get("results", [])
    assert len(resultats) == 1
    assert resultats[0]["type"] == "Participant"
