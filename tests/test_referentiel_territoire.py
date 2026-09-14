"""Villes et quartiers : une écriture, un QPV, et de quoi nettoyer.

Trois maux qui se tiennent, constatés dans le code existant :

1. **La même ville sous plusieurs écritures.** Le champ est saisi librement
   sur onze écrans. « Nogent sur Oise », « Nogent-sur-Oise », « NOGENT SUR
   OISE » et « Nogent » comptaient pour quatre villes. L'import Excel
   fabriquait même méthodiquement une variante de plus : sa propre
   normalisation produisait « Nogent Sur Oise » (title case).

2. **La liste des quartiers qui se vide sans rien dire.** Elle est filtrée
   par ÉGALITÉ DE CHAÎNE avec la ville tapée. Une variante d'écriture et
   plus aucun quartier n'apparaît — sans message.

3. **Le QPV codé dans le nom du quartier.** Faute de champ, il fallait
   écrire « Rouher (QPV Hauts de Creil) » pour que les exports retrouvent
   l'appartenance par recherche de sous-chaîne — avec DEUX règles
   différentes selon l'export, dont aucune ne reconnaissait « Cavée de
   Senlis ».

Et le geste qui manquait : supprimer un quartier doublonné était refusé dès
qu'une fiche y était rattachée. Il n'existait aucun moyen de nettoyer.
"""
import uuid

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Écriture des villes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("saisi,attendu", [
    ("creil", "Creil"),
    ("CREIL", "Creil"),
    ("Creil (60)", "Creil"),
    ("60100 Creil", "Creil"),
    ("nogent sur oise", "Nogent-sur-Oise"),
    ("NOGENT-SUR-OISE", "Nogent-sur-Oise"),
    ("Nogent Sur Oise", "Nogent-sur-Oise"),
    ("villers saint paul", "Villers-Saint-Paul"),
    ("VILLERS ST PAUL", "Villers-Saint-Paul"),
    ("saint leu d esserent", "Saint-Leu-d'Esserent"),
    ("St Leu d'Esserent", "Saint-Leu-d'Esserent"),
    ("la chapelle en serval", "La Chapelle-en-Serval"),
    ("le plessis belleville", "Le Plessis-Belleville"),
    ("les ageux", "Les Ageux"),
    ("l isle adam", "L'Isle-Adam"),
    ("verneuil en halatte", "Verneuil-en-Halatte"),
])
def test_forme_etat_civil(saisi, attendu):
    from app.services.villes import normaliser

    assert normaliser(saisi) == attendu


@pytest.mark.parametrize("saisi", ["", "   ", None])
def test_ville_vide(saisi):
    from app.services.villes import normaliser

    assert normaliser(saisi) is None


def test_une_troncature_nest_jamais_devinee():
    """« Villers » peut être Villers-Saint-Paul comme Villers-sous-Saint-Leu,
    deux communes voisines. Le code ne tranche pas ; la fusion, faite par
    quelqu'un qui connaît le territoire, oui."""
    from app.services.villes import memes_villes, normaliser

    assert normaliser("Nogent") == "Nogent"
    assert memes_villes("Nogent", "Nogent-sur-Oise") is False
    assert normaliser("Villers") == "Villers"


def test_les_variantes_decriture_se_reconnaissent():
    from app.services.villes import memes_villes

    assert memes_villes("Nogent sur Oise", "NOGENT-SUR-OISE")
    assert memes_villes("Creil (60)", "creil")
    assert memes_villes("St Leu d'Esserent", "Saint-Leu-d'Esserent")
    assert memes_villes("", "Creil") is False


def test_limport_excel_ne_fabrique_plus_sa_propre_variante():
    """Sa normalisation produisait « Nogent Sur Oise » (title case), là où
    la saisie manuelle donne « Nogent-sur-Oise »."""
    from app.ateliers.excel_import import normalize_ville

    assert normalize_ville("nogent sur oise") == "Nogent-sur-Oise"
    assert normalize_ville("CREIL (60)") == "Creil"
    assert normalize_ville("") is None


def test_ville_normalisee_a_la_saisie(admin_client, app):
    suf = _suffixe()
    admin_client.post("/participants/new", data={
        "nom": f"Ville{suf}", "prenom": "Test",
        "ville": "nogent  sur   oise", "force_creation": "1",
    }, follow_redirects=True)

    with app.app_context():
        from app.models import Participant

        fiche = Participant.query.filter_by(nom=f"Ville{suf}").one()
        assert fiche.ville == "Nogent-sur-Oise"


# ---------------------------------------------------------------------------
# QPV : un champ, plus une convention de nommage
# ---------------------------------------------------------------------------

def test_deduction_du_qpv_depuis_les_noms_bricoles():
    """Reprise de l'existant : les noms disent déjà ce qu'il faut savoir."""
    from app.services.referentiels import deduire_qpv_depuis_le_nom, nom_sans_qpv

    assert deduire_qpv_depuis_le_nom("Rouher (QPV Hauts de Creil)") == "Hauts de Creil"
    assert deduire_qpv_depuis_le_nom("Autres Hauts de Creil (QPV Hauts de Creil)") == "Hauts de Creil"
    assert deduire_qpv_depuis_le_nom("Hauts de Creil") == "Hauts de Creil"
    assert deduire_qpv_depuis_le_nom("Rouher", is_qpv=True) == "Hauts de Creil"
    assert deduire_qpv_depuis_le_nom("Cavée de Senlis") is None
    assert deduire_qpv_depuis_le_nom("") is None

    assert nom_sans_qpv("Rouher (QPV Hauts de Creil)") == "Rouher"
    assert nom_sans_qpv("Rouher") == "Rouher"


def test_deux_quartiers_du_meme_qpv_restent_distincts(app):
    """Le cœur du besoin : distinguer le Rouher des autres quartiers des
    Hauts de Creil, tout en les additionnant quand le financeur demande le
    QPV. Avant, il fallait fabriquer deux faux quartiers dont le nom portait
    le QPV entre parenthèses."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Quartier

        suf = _suffixe()
        rouher = Quartier(ville=f"Creil{suf}", nom="Rouher", qpv="Hauts de Creil", is_qpv=True)
        cavee = Quartier(ville=f"Creil{suf}", nom="Cavée de Senlis", qpv="Hauts de Creil", is_qpv=True)
        db.session.add_all([rouher, cavee])
        db.session.flush()

        # Un nom sans le moindre mot-clé : « Cavée de Senlis » était
        # auparavant classée « Hors QPV » par l'export SENACS et « Autres »
        # par l'export XLSX.
        habitant = Participant(nom=f"Cavee{suf}", prenom="A", quartier_id=cavee.id)
        db.session.add(habitant)
        db.session.commit()

        from app.services.senacs import _bucket_quartier
        from app.statsimpact.exports_xlsx import _quartier_bucket

        assert habitant.is_qpv is True
        assert _bucket_quartier(habitant) == "Hauts de Creil"
        assert _quartier_bucket(cavee) == "Hauts de Creil"
        # Et le Rouher garde sa ligne à lui.
        assert _quartier_bucket(rouher) == "Rouher"

        voisin = Participant(nom=f"Rouher{suf}", prenom="B", quartier_id=rouher.id)
        db.session.add(voisin)
        db.session.commit()
        assert _bucket_quartier(voisin) == "Rouher"


def test_renseigner_un_qpv_coche_la_case(admin_client, app):
    """Personne n'a à dire deux fois la même chose."""
    suf = _suffixe()
    admin_client.post("/quartiers/new", data={
        "ville": "creil", "nom": f"Quartier{suf}", "qpv": "Hauts de Creil",
    }, follow_redirects=True)

    with app.app_context():
        from app.models import Quartier

        q = Quartier.query.filter_by(nom=f"Quartier{suf}").one()
        assert q.qpv == "Hauts de Creil"
        assert q.is_qpv is True
        # La ville est passée à la forme d'état civil au passage.
        assert q.ville == "Creil"


# ---------------------------------------------------------------------------
# Fusion : le geste qui manquait
# ---------------------------------------------------------------------------

def test_doublons_de_quartiers_reperes(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Quartier
        from app.services.referentiels import doublons_de_quartiers

        suf = _suffixe()
        ville = f"Creil{suf}"
        db.session.add_all([
            Quartier(ville=ville, nom="Rouher (QPV Hauts de Creil)"),
            Quartier(ville=ville, nom="ROUHER"),
            Quartier(ville=ville, nom="Cavée de Senlis"),
        ])
        db.session.commit()

        groupes = [g for g in doublons_de_quartiers()
                   if any(q.ville == ville for q in g)]
        assert len(groupes) == 1
        assert {q.nom for q in groupes[0]} == {"Rouher (QPV Hauts de Creil)", "ROUHER"}


def test_fusion_deplace_les_fiches_puis_supprime(app):
    """Supprimer un quartier lié à des fiches était refusé, sans autre
    chemin proposé : un doublon créé un jour de rush restait pour toujours,
    et les bilans comptaient le même quartier deux fois."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Quartier
        from app.services.referentiels import fusionner_quartiers

        suf = _suffixe()
        ville = f"Creil{suf}"
        source = Quartier(ville=ville, nom="ROUHER", qpv="Hauts de Creil",
                          is_qpv=True, latitude=49.2, longitude=2.5)
        cible = Quartier(ville=ville, nom="Rouher")
        db.session.add_all([source, cible])
        db.session.flush()
        source_id, cible_id = source.id, cible.id
        for i in range(3):
            db.session.add(Participant(nom=f"Fusion{suf}", prenom=f"P{i}", quartier_id=source_id))
        db.session.commit()

        deplaces = fusionner_quartiers(source, cible)

        assert deplaces == 3
        assert db.session.get(Quartier, source_id) is None
        survivant = db.session.get(Quartier, cible_id)
        assert Participant.query.filter_by(quartier_id=cible_id).count() == 3
        # Rien de ce que le doublon savait n'est perdu.
        assert survivant.qpv == "Hauts de Creil"
        assert survivant.is_qpv is True
        assert survivant.latitude == 49.2


def test_fusion_de_quartiers_depuis_lecran(admin_client, app):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Quartier

        suf = _suffixe()
        ville = f"Creil{suf}"
        a = Quartier(ville=ville, nom="Gournay")
        b = Quartier(ville=ville, nom="GOURNAY")
        db.session.add_all([a, b])
        db.session.flush()
        db.session.add(Participant(nom=f"Gournay{suf}", prenom="X", quartier_id=b.id))
        db.session.commit()
        source_id, cible_id = b.id, a.id

    r = admin_client.post("/quartiers/fusionner",
                          data={"source_id": source_id, "cible_id": cible_id},
                          follow_redirects=True)
    assert r.status_code == 200
    assert "fiche(s) déplacée(s)" in r.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Quartier

        assert db.session.get(Quartier, source_id) is None
        assert Participant.query.filter_by(quartier_id=cible_id).count() == 1


def test_un_quartier_ne_se_fusionne_pas_avec_lui_meme(admin_client, app):
    with app.app_context():
        from app.extensions import db
        from app.models import Quartier

        q = Quartier(ville=f"Creil{_suffixe()}", nom="Seul")
        db.session.add(q)
        db.session.commit()
        qid = q.id

    r = admin_client.post("/quartiers/fusionner", data={"source_id": qid, "cible_id": qid},
                          follow_redirects=True)
    assert "ne peut pas se fusionner avec lui-même" in r.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Quartier

        assert db.session.get(Quartier, qid) is not None


def test_fusion_de_villes(admin_client, app):
    """« Nogent » -> « Nogent-sur-Oise » sur toutes les fiches d'un coup."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant, Quartier

        suf = _suffixe()
        source = f"Nogent{suf}"
        cible = f"Nogent-sur-Oise{suf}"
        for i in range(2):
            db.session.add(Participant(nom=f"Nog{suf}", prenom=f"P{i}", ville=source))
        db.session.add(Quartier(ville=source, nom=f"Centre{suf}"))
        db.session.commit()

    r = admin_client.post("/quartiers/villes/fusionner",
                          data={"source": source, "cible": cible}, follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        from app.models import Participant, Quartier

        assert Participant.query.filter_by(ville=source).count() == 0
        assert Participant.query.filter_by(ville=cible).count() == 2
        assert Quartier.query.filter_by(ville=cible).count() == 1


def test_ecran_des_villes_montre_le_poids_de_chaque_ecriture(admin_client, app):
    """On ne demande à personne de fusionner à l'aveugle."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        suf = _suffixe()
        for i in range(3):
            db.session.add(Participant(nom=f"Poids{suf}", prenom=f"P{i}", ville=f"Creil{suf}"))
        db.session.commit()

    page = admin_client.get("/quartiers/villes").get_data(as_text=True)
    assert f"Creil{suf}" in page
    assert "Toutes les villes enregistrées" in page
    # L'explication du symptôme silencieux est à l'écran, pas seulement
    # dans le code.
    assert "vide la liste" in page


def test_villes_utilisees_signale_ce_qui_nest_pas_conforme(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.referentiels import villes_utilisees

        suf = _suffixe()
        db.session.add(Participant(nom=f"Conf{suf}", prenom="A", ville=f"nogent sur oise{suf}"))
        db.session.commit()

        entrees = {v["nom"]: v for v in villes_utilisees()}
        entree = entrees[f"nogent sur oise{suf}"]
        assert entree["a_normaliser"] is True
        assert entree["forme_propre"].startswith("Nogent-sur-Oise")
        assert entree["participants"] == 1


def test_reprise_des_villes_deja_saisies(app):
    """Ce que fait la migration sur une base déjà remplie : elle uniformise
    les variantes d'écriture, et SEULEMENT elles."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.referentiels import normaliser_villes_existantes

        suf = _suffixe()
        # Même suffixe attaché de la même façon dans les trois : ce qui
        # doit diverger, c'est la troncature, pas le jeu de test.
        saisies = [f"nogent sur oise {suf}", f"NOGENT-SUR-OISE {suf}", f"nogent {suf}"]
        for i, ville in enumerate(saisies):
            db.session.add(Participant(nom=f"Reprise{suf}", prenom=f"P{i}", ville=ville))
        db.session.commit()

        normaliser_villes_existantes(db.session.connection())
        db.session.commit()

        villes = [p.ville for p in Participant.query.filter_by(nom=f"Reprise{suf}")
                  .order_by(Participant.prenom).all()]

    # Les deux variantes d'écriture se rejoignent…
    assert villes[0] == villes[1]
    assert villes[0].startswith("Nogent-sur-Oise")
    # …mais la troncature n'est jamais complétée : c'est à quelqu'un de
    # décider si « Nogent » veut dire Nogent-sur-Oise.
    assert "sur-Oise" not in villes[2]
    assert villes[2] != villes[0]
