"""L'extension ``unaccent`` de PostgreSQL, et ce qui se passe sans elle.

Sur SQLite, la recherche compare sans accents grâce à une fonction Python
posée sur la connexion. Sur PostgreSQL, il faut l'extension ``unaccent``.
Son installation demande des droits que le compte applicatif de la
production n'a pas forcément — nous avons déjà vu ce serveur refuser un
simple ``ALTER TABLE``.

Deux exigences, donc, et la seconde compte autant que la première :

1. quand l'extension est là, « amelie » trouve « Amélie » ;
2. quand elle n'est pas là, **la recherche continue de répondre**. Une
   extension manquante doit coûter un peu de confort, jamais une barre de
   recherche muette — la panne dont on sort.
"""
import uuid


def _suffixe():
    return uuid.uuid4().hex[:6]


def _chercher(admin_client, terme):
    r = admin_client.get(f"/api/global-search?q={terme}")
    assert r.status_code == 200, r.status_code
    return r.get_json().get("results", [])


# ---------------------------------------------------------------------------
# Le sondage
# ---------------------------------------------------------------------------

def test_le_sondage_ne_dit_oui_que_sur_postgresql(app, dialecte):
    """Sur SQLite, la réponse est non : c'est l'autre mécanisme qui joue.

    Répondre oui y ferait construire du SQL appelant ``unaccent()``, que
    SQLite ne connaît pas — la panne d'origine, à l'envers.
    """
    with app.app_context():
        from app.extensions import db
        from app.services.recherche_texte import unaccent_disponible

        disponible = unaccent_disponible(db.engine)

    if dialecte != "postgresql":
        assert disponible is False
    else:
        assert disponible in (True, False)


def test_le_sondage_ne_leve_jamais(app):
    """Un moteur qui explose à la connexion rend « non », pas une exception.

    Le sondage tourne dans le chemin de la barre de recherche : s'il peut
    lever, il peut casser la recherche.
    """
    from app.services.recherche_texte import oublier_le_sondage, unaccent_disponible

    class MoteurCasse:
        class dialect:
            name = "postgresql"

        url = "postgresql://sonde-de-test/neant"

        def connect(self):
            raise RuntimeError("serveur injoignable")

    oublier_le_sondage()
    try:
        assert unaccent_disponible(MoteurCasse()) is False
    finally:
        oublier_le_sondage()


def test_le_sondage_ne_se_refait_pas_a_chaque_frappe(app):
    """Il ouvre une connexion : le refaire à chaque lettre tapée coûterait
    plus cher que la recherche."""
    from app.services.recherche_texte import oublier_le_sondage, unaccent_disponible

    appels = {"n": 0}

    class MoteurCompteur:
        class dialect:
            name = "postgresql"

        url = "postgresql://sonde-de-test/compteur"

        def connect(self):
            appels["n"] += 1
            raise RuntimeError("peu importe")

    oublier_le_sondage()
    try:
        moteur = MoteurCompteur()
        for _ in range(5):
            unaccent_disponible(moteur)
        assert appels["n"] == 1
    finally:
        oublier_le_sondage()


# ---------------------------------------------------------------------------
# Ce qui doit rester vrai sans l'extension
# ---------------------------------------------------------------------------

def test_sans_extension_la_recherche_repond_quand_meme(admin_client, app, monkeypatch):
    """LA garantie. On force le sondage à dire non et on vérifie que la
    recherche fonctionne — accents tapés, casse ignorée."""
    import app.main.recherche as recherche

    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        db.session.add(Participant(nom=f"Bénézit{suf}", prenom="Amélie"))
        db.session.commit()

    monkeypatch.setattr(recherche, "_compare_sans_accent_en_base", lambda: False)

    # Tapé tel qu'enregistré : doit se trouver sur n'importe quel moteur.
    assert _chercher(admin_client, f"Bénézit{suf}")
    # Et la casse ne doit pas compter, même sans l'extension.
    assert _chercher(admin_client, f"bénézit{suf}")


def test_avec_extension_la_recherche_repond_aussi(admin_client, app, monkeypatch, dialecte):
    """L'inverse : on force le sondage à dire oui.

    Sur PostgreSQL sans l'extension, cela construit du SQL appelant une
    fonction absente — et c'est exactement ce qu'on veut savoir, parce
    qu'alors la vraie production planterait de la même façon.
    """
    if dialecte != "postgresql":
        import pytest

        pytest.skip("le chemin unaccent n'existe que sur PostgreSQL")

    with app.app_context():
        from app.extensions import db
        from app.services.recherche_texte import unaccent_disponible

        if not unaccent_disponible(db.engine):
            import pytest

            pytest.skip("extension unaccent absente de ce serveur")

    import app.main.recherche as recherche

    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        db.session.add(Participant(nom=f"Bénézit{suf}", prenom="Amélie"))
        db.session.commit()

    monkeypatch.setattr(recherche, "_compare_sans_accent_en_base", lambda: True)

    assert _chercher(admin_client, f"benezit{suf}")
    assert _chercher(admin_client, f"Bénézit{suf}")
    assert _chercher(admin_client, f"BENEZIT{suf}")


# ---------------------------------------------------------------------------
# Le diagnostic à l'écran
# ---------------------------------------------------------------------------

def test_la_page_sante_dit_si_la_recherche_est_tolerante(admin_client):
    """Sans cette ligne, une extension refusée ne se voit nulle part : la
    recherche répond, elle trouve seulement moins — et personne ne sait
    pourquoi."""
    r = admin_client.get("/admin/sante")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Recherche sans accents" in page
