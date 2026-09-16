"""Le genre : une valeur enregistrée, un libellé qui suit l'âge.

Constat de départ, mesuré sur le code existant : HUIT endroits classaient
le genre — six tables de correspondance et deux comptages bruts — et le même
mot ne donnait pas le même comptage selon l'écran.

    saisi       tableau de bord   indicateurs   stats impact            SENACS
    Fille       Non renseigné     femme         Femmes                  Fille
    Garçon      Non renseigné     inconnu       Autre / non renseigné   Garçon
    Préférez…   Non renseigné     inconnu       Autre / non renseigné   Préférez…

Un « Garçon » n'était compté comme garçon nulle part. Et « Préférez ne pas
répondre » — une valeur que l'application proposait elle-même dans deux de
ses formulaires — était rangée de trois façons différentes.

La convention du centre : avant 18 ans révolus on dit fille et garçon,
ensuite femme et homme. Le public va de quatre mois à plus de quatre-vingt
dix ans, donc les deux cohabitent en permanence.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Normalisation : tout ce qui a pu être tapé depuis le début
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("saisi", [
    "F", "f", "Femme", "FEMME", "femme", "Féminin", "feminin", "Fille", "filles",
    "Mme", "Madame", "female", "woman",
])
def test_tout_le_vocabulaire_feminin_tombe_sur_un_seul_code(saisi):
    from app.services.genre import FEMININ, normaliser

    assert normaliser(saisi) == FEMININ


@pytest.mark.parametrize("saisi", [
    "H", "h", "M", "Homme", "HOMME", "Masculin", "Garçon", "garcon", "garçons",
    "Mr", "Monsieur", "male", "man",
])
def test_tout_le_vocabulaire_masculin_tombe_sur_un_seul_code(saisi):
    from app.services.genre import MASCULIN, normaliser

    assert normaliser(saisi) == MASCULIN


def test_les_valeurs_proposees_par_lapplication_sont_reconnues():
    """« Préférez ne pas répondre » était offert par deux formulaires et
    classé de trois façons différentes en aval."""
    from app.services.genre import SANS_REPONSE, AUTRE, normaliser

    assert normaliser("Préférez ne pas répondre") == SANS_REPONSE
    assert normaliser("Préfère ne pas répondre") == SANS_REPONSE
    assert normaliser("Autre") == AUTRE
    assert normaliser("non binaire") == AUTRE


@pytest.mark.parametrize("saisi", ["", "   ", None, "zzz", "42", "???"])
def test_une_saisie_illisible_ne_se_devine_pas(saisi):
    """Compter quelqu'un dans la mauvaise colonne est pire que de l'afficher
    « non renseigné » : l'erreur devient invisible."""
    from app.services.genre import normaliser

    assert normaliser(saisi) is None


def test_saisie_bavarde():
    """« femme (mère de famille) » : le premier mot tranche…"""
    from app.services.genre import FEMININ, MASCULIN, normaliser

    assert normaliser("femme (mère de famille)") == FEMININ
    assert normaliser("homme 45 ans") == MASCULIN


def test_une_initiale_isolee_nest_pas_un_repli():
    """…mais seulement s'il fait au moins trois lettres.

    Sinon « n'importe quoi » serait lu « n », donc « ne se prononce pas » :
    une saisie illisible deviendrait une réponse, et personne ne verrait
    jamais qu'il faut la corriger.
    """
    from app.services.genre import normaliser

    assert normaliser("n'importe quoi") is None
    assert normaliser("f. quelque chose") is None
    assert normaliser("a voir avec la famille") is None
    # Seules, ces lettres gardent tout leur sens — la ponctuation est du
    # bruit, « h ? » se réduit à « h » et vaut bien masculin.
    assert normaliser("n") == "N"
    assert normaliser("f") == "F"
    assert normaliser("h ?") == "H"


# ---------------------------------------------------------------------------
# Le libellé suit l'âge
# ---------------------------------------------------------------------------

def test_fille_avant_dix_huit_ans_femme_apres():
    from app.services.genre import libelle

    assert libelle("F", 4) == "Fille"
    assert libelle("F", 17) == "Fille"
    assert libelle("F", 18) == "Femme"      # le jour même de l'anniversaire
    assert libelle("F", 92) == "Femme"
    assert libelle("H", 8) == "Garçon"
    assert libelle("H", 18) == "Homme"


def test_age_inconnu_retombe_sur_la_forme_adulte():
    """Un âge inconnu n'est pas un enfant."""
    from app.services.genre import libelle

    assert libelle("F", None) == "Femme"
    assert libelle("H") == "Homme"


def test_pluriels_pour_les_tableaux():
    from app.services.genre import libelle

    assert libelle("F", 10, pluriel=True) == "Filles"
    assert libelle("H", 40, pluriel=True) == "Hommes"


def test_personne_ne_repasse_sur_une_fiche_le_jour_des_dix_huit_ans(app):
    """C'est tout l'intérêt : la donnée ne change pas, le mot si."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.genre import libelle_participant

        p = Participant(nom=f"Majorite{_suffixe()}", prenom="Inès", genre="F",
                        date_naissance=date(2008, 6, 15))
        db.session.add(p)
        db.session.commit()

        assert libelle_participant(p, date(2026, 6, 14)) == "Fille"
        assert libelle_participant(p, date(2026, 6, 15)) == "Femme"
        # Et la valeur enregistrée, elle, n'a pas bougé d'un octet.
        assert p.genre == "F"


def test_un_bilan_passe_ne_derive_pas_avec_le_temps(app):
    """Le bilan 2020 doit redonner les mêmes chiffres en 2030."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.genre import libelle_participant

        p = Participant(nom=f"Bilan{_suffixe()}", prenom="Tom", genre="H",
                        date_naissance=date(2010, 3, 1))
        db.session.add(p)
        db.session.commit()

        assert libelle_participant(p, date(2020, 12, 31)) == "Garçon"
        assert libelle_participant(p, date(2030, 12, 31)) == "Homme"


# ---------------------------------------------------------------------------
# Répartition et colonnes
# ---------------------------------------------------------------------------

def test_repartition_garde_les_colonnes_vides(app):
    """Un tableau où « Garçons » disparaît les années sans garçon se lit mal
    d'une année sur l'autre, et laisse croire à un oubli de saisie."""
    with app.app_context():
        from app.models import Participant
        from app.services.genre import ordre_pluriels, repartition

        gens = [
            Participant(nom="A", prenom="a", genre="F", date_naissance=date(2015, 1, 1)),
            Participant(nom="B", prenom="b", genre="F", date_naissance=date(1980, 1, 1)),
            Participant(nom="C", prenom="c", genre=None),
        ]
        comptes = repartition(gens, date(2026, 1, 1))

    assert list(comptes) == ordre_pluriels()
    assert comptes["Filles"] == 1
    assert comptes["Femmes"] == 1
    assert comptes["Garçons"] == 0
    assert comptes["Non renseigné"] == 1


def test_repartition_sans_mineurs_pour_les_tableaux_a_trois_colonnes(app):
    """Certains tableaux réglementaires ne demandent que femmes et hommes."""
    with app.app_context():
        from app.models import Participant
        from app.services.genre import repartition

        gens = [Participant(nom="A", prenom="a", genre="F", date_naissance=date(2015, 1, 1))]
        comptes = repartition(gens, date(2026, 1, 1), mineurs=False)

    assert comptes["Femmes"] == 1
    assert "Filles" not in comptes


def test_cles_de_filtre_sans_accent_ni_espace(app):
    """Elles traversent une URL, un signet et un export sans se déformer."""
    from app.services.genre import GROUPES, groupe

    assert groupe("F", 10) == "filles"
    assert groupe("H", 10) == "garcons"
    assert groupe("F", 40) == "femmes"
    assert groupe(None) == "inconnu"
    for cle in GROUPES:
        assert cle == cle.lower()
        assert " " not in cle
        assert cle.isascii()


def test_choix_du_formulaire_suivent_lage():
    """Sur la fiche d'un enfant, on coche « Fille » ; sur celle d'un adulte,
    « Femme ». C'est la même donnée enregistrée."""
    from app.services.genre import choix

    assert dict(choix(8))["F"] == "Fille"
    assert dict(choix(40))["F"] == "Femme"
    assert dict(choix(8))["H"] == "Garçon"


# ---------------------------------------------------------------------------
# Les huit classements disent enfin la même chose
# ---------------------------------------------------------------------------

def test_les_moteurs_statistiques_saccordent(app):
    """Le cœur du problème : « Fille » était « Non renseigné » ici, « femme »
    là, « Femmes » ailleurs et une ligne à part dans l'export SENACS."""
    with app.app_context():
        from app.models import Participant
        from app.services.dashboard_service import _normalize_gender
        from app.services.indicators import _gender_key
        from app.statsimpact.engine import _participant_genre_bucket

        for saisi in ("Fille", "F", "Femme", "FEMME", "Féminin"):
            fiche = Participant(nom="X", prenom="x", genre=saisi)
            assert _normalize_gender(saisi, 10) == "Filles"
            assert _normalize_gender(saisi, 40) == "Femmes"
            assert _gender_key(saisi) == "femme"
            assert _participant_genre_bucket(fiche) == "Femmes"

        for saisi in ("Garçon", "H", "Homme", "M", "Masculin"):
            fiche = Participant(nom="X", prenom="x", genre=saisi)
            assert _normalize_gender(saisi, 10) == "Garçons"
            assert _gender_key(saisi) == "homme"
            assert _participant_genre_bucket(fiche) == "Hommes"


def test_un_garcon_est_enfin_compte_comme_un_garcon(app):
    """Régression nommée : avant, « Garçon » tombait dans « Non renseigné »
    sur le tableau de bord, « inconnu » dans les indicateurs et
    « Autre / non renseigné » dans les stats d'impact."""
    with app.app_context():
        from app.models import Participant
        from app.services.dashboard_service import _normalize_gender
        from app.services.indicators import _gender_key
        from app.statsimpact.engine import _participant_genre_bucket

        assert _normalize_gender("Garçon", 9) == "Garçons"
        assert _gender_key("Garçon") == "homme"
        assert _participant_genre_bucket(Participant(nom="X", prenom="x", genre="Garçon")) == "Hommes"


# ---------------------------------------------------------------------------
# Reprise des données déjà saisies
# ---------------------------------------------------------------------------

def test_normalisation_dune_colonne_existante(app):
    """Ce que fait la migration sur une base déjà remplie."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.genre import normaliser_colonne

        suf = _suffixe()
        # Toutes ces valeurs tiennent dans la colonne (String(20)).
        # « Préférez ne pas répondre » fait 24 caractères : SQLite l'aurait
        # stockée quand même, PostgreSQL la refuse — et c'est lui qui a
        # raison. Sa reconnaissance est vérifiée sans base, plus haut.
        anciennes = ["Femme", "F", "Fille", "Homme", "Garçon", "Autre",
                     "nsp", "n'importe quoi"]
        for i, valeur in enumerate(anciennes):
            db.session.add(Participant(nom=f"Reprise{suf}", prenom=f"P{i}", genre=valeur))
        db.session.commit()

        normaliser_colonne(db.session.connection(), "participant", "genre")
        db.session.commit()

        codes = [p.genre for p in Participant.query.filter_by(nom=f"Reprise{suf}")
                 .order_by(Participant.prenom).all()]

    assert codes == ["F", "F", "F", "H", "H", "A", "N", None]


def test_formulaires_offrent_le_meme_choix(admin_client, app):
    """Cinq vocabulaires de saisie cohabitaient. Un seul désormais."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        p = Participant(nom=f"Widget{_suffixe()}", prenom="Test", genre="F",
                        date_naissance=date(2016, 5, 5))
        db.session.add(p)
        db.session.commit()
        pid = p.id

    page = admin_client.get(f"/participants/{pid}/edit").get_data(as_text=True)
    # Un enfant : le formulaire propose « Fille » et « Garçon »…
    assert '<option value="F"' in page
    assert "Fille" in page and "Garçon" in page
    # …et l'ancien champ de texte libre a disparu de la fiche d'activité.
    assert 'placeholder="F / H / ..."' not in page
