"""Ce qui s'imprime sur une feuille d'émargement.

Régression attrapée en répondant à une question sur le pré-émargement : la
colonne « sexe » de la feuille imprimée affichait la valeur BRUTE de la
fiche participant. Depuis que le genre est enregistré sous forme de code
(F / H / A / N), cela revenait à imprimer « F » sur un justificatif remis à
un financeur.

L'âge est pris au JOUR DE LA SÉANCE : une feuille réimprimée deux ans plus
tard doit dire ce qu'elle disait le jour même.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


def test_la_feuille_imprime_le_mot_pas_le_code(app):
    with app.app_context():
        from app.activite.services.docx_utils import _genre_lisible
        from app.models import Participant

        adulte = Participant(nom="A", prenom="a", genre="F", date_naissance=date(1980, 5, 1))
        enfant = Participant(nom="B", prenom="b", genre="H", date_naissance=date(2015, 5, 1))

        assert _genre_lisible(adulte, date(2026, 6, 1)) == "Femme"
        assert _genre_lisible(enfant, date(2026, 6, 1)) == "Garçon"
        # Jamais le code brut.
        assert _genre_lisible(adulte, date(2026, 6, 1)) not in {"F", "H"}


def test_lage_est_celui_du_jour_de_la_seance(app):
    """Une feuille réimprimée plus tard ne doit pas changer de mot."""
    with app.app_context():
        from app.activite.services.docx_utils import _genre_lisible
        from app.models import Participant

        p = Participant(nom="C", prenom="c", genre="F", date_naissance=date(2008, 6, 15))
        assert _genre_lisible(p, date(2025, 6, 14)) == "Fille"   # la veille de ses 18 ans
        assert _genre_lisible(p, date(2026, 6, 15)) == "Femme"


def test_anciennes_saisies_toujours_lisibles(app):
    """Une base pas encore reprise contient « Femme », « Fille », « M »…"""
    with app.app_context():
        from app.activite.services.docx_utils import _genre_lisible
        from app.models import Participant

        for saisi, attendu in (("Femme", "Femme"), ("Fille", "Femme"), ("M", "Homme"),
                               ("Garçon", "Homme"), (None, "Non renseigné")):
            p = Participant(nom="D", prenom="d", genre=saisi, date_naissance=date(1980, 1, 1))
            assert _genre_lisible(p, date(2026, 1, 1)) == attendu


def test_export_des_bulletins_porte_le_mot(app):
    with app.app_context():
        from app.services.inscriptions_annuelles import _libelle_genre

        assert _libelle_genre("F", date(1980, 3, 1)) == "Femme"
        assert _libelle_genre("F", date(2015, 3, 1)) == "Fille"
        assert _libelle_genre("H", None) == "Homme"
        assert _libelle_genre(None, None) == "Non renseigné"


def test_la_feuille_docx_ne_contient_pas_le_code(app, tmp_path):
    """Bout en bout : on génère le document et on relit ce qu'il contient."""
    docx = pytest.importorskip("docx")

    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
        from app.activite.services.docx_utils import generate_collectif_docx_pdf

        suf = _suffixe()
        atelier = AtelierActivite(secteur="Adultes", nom=f"Feuille{suf}")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(atelier_id=atelier.id, secteur="Adultes",
                                 session_type="COLLECTIF", date_session=date(2026, 6, 1),
                                 heure_debut="14:00", heure_fin="16:00")
        db.session.add(seance)
        db.session.flush()
        femme = Participant(nom=f"Adulte{suf}", prenom="Ana", genre="F",
                            date_naissance=date(1980, 5, 1))
        garcon = Participant(nom=f"Jeune{suf}", prenom="Leo", genre="H",
                             date_naissance=date(2015, 5, 1))
        db.session.add_all([femme, garcon])
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=seance.id, participant_id=femme.id),
            PresenceActivite(session_id=seance.id, participant_id=garcon.id),
        ])
        db.session.commit()

        chemin_docx, _ = generate_collectif_docx_pdf(
            app=app, atelier=atelier, session=seance)

        texte = "\n".join(
            cellule.text
            for table in docx.Document(chemin_docx).tables
            for ligne in table.rows
            for cellule in ligne.cells
        )

    assert "Femme" in texte, "l'adulte doit être imprimée « Femme »"
    assert "Garçon" in texte, "le mineur doit être imprimé « Garçon »"
    # Et surtout : jamais le code de genre seul dans une cellule.
    #
    # On ne teste QUE « F » : la feuille porte légitimement des cellules
    # « H », mais c'est la colonne TYPE DE PUBLIC (H = Habitant, puis S, B,
    # A, P), qui n'a rien à voir avec le genre. « F » n'appartient à aucun
    # autre référentiel de ce document.
    cellules = [c.strip() for c in texte.split("\n")]
    assert "F" not in cellules, \
        "le code de genre ne doit jamais atterrir sur un justificatif"
