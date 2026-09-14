"""Tests du bilan SENACS (publics dédoublonnés, tranches d'âge, actions)."""
import uuid
from datetime import date
from io import BytesIO

import pytest

ANNEE = 2025


@pytest.fixture(scope="module")
def donnees_senacs(app):
    """Jeu de données 2025 : 3 participants, 2 ateliers de secteurs différents.

    - Amina (née 2000, Femme, Rouher) : présente aux DEUX ateliers
      -> doit compter pour 1 participant unique et 2 participations.
    - Bruno (né 1950, Homme, sans quartier) : présent à un atelier.
    - Chloé (née 2020) : présente à un atelier.
    - Une présence en 2024 qui ne doit PAS compter pour 2025.
    """
    suffixe = uuid.uuid4().hex[:6]
    with app.app_context():
        from app.extensions import db
        from app.models import (
            AtelierActivite,
            Participant,
            Partenaire,
            PresenceActivite,
            Quartier,
            SessionActivite,
        )

        rouher = Quartier.query.filter(Quartier.nom.ilike("%rouher%")).first()
        if rouher is None:
            rouher = Quartier(nom="Rouher", ville="Creil", is_qpv=True)
            db.session.add(rouher)
            db.session.flush()

        amina = Participant(nom=f"Senacs-A-{suffixe}", prenom="Amina", genre="Femme",
                            date_naissance=date(2000, 5, 1), quartier_id=rouher.id, ville="Creil")
        bruno = Participant(nom=f"Senacs-B-{suffixe}", prenom="Bruno", genre="Homme",
                            date_naissance=date(1950, 1, 15))
        chloe = Participant(nom=f"Senacs-C-{suffixe}", prenom="Chloé",
                            date_naissance=date(2020, 9, 9))
        db.session.add_all([amina, bruno, chloe])

        at_num = AtelierActivite(nom=f"Atelier num {suffixe}", secteur="Numérique")
        at_fam = AtelierActivite(nom=f"Atelier fam {suffixe}", secteur="Familles")
        db.session.add_all([at_num, at_fam])
        db.session.flush()

        s_num = SessionActivite(atelier_id=at_num.id, secteur="Numérique",
                                date_session=date(ANNEE, 3, 10), duree_minutes=120)
        s_fam = SessionActivite(atelier_id=at_fam.id, secteur="Familles",
                                date_session=date(ANNEE, 4, 20), duree_minutes=90)
        s_2024 = SessionActivite(atelier_id=at_num.id, secteur="Numérique",
                                 date_session=date(ANNEE - 1, 6, 1), duree_minutes=60)
        db.session.add_all([s_num, s_fam, s_2024])
        db.session.flush()

        db.session.add_all([
            PresenceActivite(session_id=s_num.id, participant_id=amina.id),
            PresenceActivite(session_id=s_fam.id, participant_id=amina.id),
            PresenceActivite(session_id=s_num.id, participant_id=bruno.id),
            PresenceActivite(session_id=s_fam.id, participant_id=chloe.id),
            PresenceActivite(session_id=s_2024.id, participant_id=bruno.id),
        ])
        db.session.add(Partenaire(nom=f"CAF Oise {suffixe}"))
        db.session.commit()

        return {
            "suffixe": suffixe,
            "amina_id": amina.id,
            "atelier_num": at_num.nom,
            "atelier_fam": at_fam.nom,
        }


def test_dedoublonnage_inter_secteurs(app, donnees_senacs):
    with app.app_context():
        from app.services.senacs import publics_annee

        publics = publics_annee(ANNEE)
        # Amina (2 présences) ne compte qu'une fois : 3 uniques, 4 participations
        assert publics["participants_uniques"] == 3
        assert publics["participations"] == 4


def test_tranches_age_senacs(app, donnees_senacs):
    with app.app_context():
        from app.services.senacs import publics_annee

        ages = publics_annee(ANNEE)["ages"]
        assert ages["18-25 ans"] == 1, "Amina née en 2000 a 25 ans au 31/12/2025"
        assert ages["75 ans et plus"] == 1, "Bruno né en 1950 a 75 ans au 31/12/2025"
        assert ages["0-6 ans"] == 1, "Chloé née en 2020 a 5 ans au 31/12/2025"


def test_genres_et_quartiers(app, donnees_senacs):
    with app.app_context():
        from app.services.senacs import publics_annee

        publics = publics_annee(ANNEE)
        # Colonnes du référentiel, au pluriel, comptées à l'âge du 31/12.
        # Amina a 25 ans, Bruno 75 : deux adultes. Chloé n'a pas de genre.
        assert publics["genres"]["Femmes"] == 1
        assert publics["genres"]["Hommes"] == 1
        assert publics["genres"]["Non renseigné"] == 1
        # Les colonnes vides existent quand même : un tableau où « Filles »
        # disparaît une année se lit mal d'une année sur l'autre.
        assert publics["genres"]["Filles"] == 0
        assert publics["quartiers"]["Rouher"] == 1


def test_un_mineur_est_compte_comme_fille_ou_garcon(app, donnees_senacs):
    """La convention du centre : avant 18 ans, on dit fille et garçon.

    Et elle est calculée à la date du bilan — pas à aujourd'hui. Une jeune
    de 17 ans au 31/12/2025 reste une « fille » dans le bilan 2025, même
    consulté des années plus tard.
    """
    import uuid
    from datetime import date as _date

    with app.app_context():
        from app.extensions import db
        from app.models import Participant, PresenceActivite, SessionActivite
        from app.services.senacs import publics_annee

        seance = SessionActivite.query.filter(
            SessionActivite.date_session.between(_date(ANNEE, 1, 1), _date(ANNEE, 12, 31))
        ).first()
        assert seance is not None

        jeune = Participant(
            nom=f"Senacs-D-{uuid.uuid4().hex[:6]}", prenom="Inès",
            genre="F", date_naissance=_date(ANNEE - 17, 6, 1),
        )
        db.session.add(jeune)
        db.session.flush()
        presence = PresenceActivite(session_id=seance.id, participant_id=jeune.id)
        db.session.add(presence)
        db.session.commit()

        genres = publics_annee(ANNEE)["genres"]
        assert genres["Filles"] == 1, "17 ans au 31/12 : c'est une fille"
        assert genres["Femmes"] == 1, "Amina, 25 ans, reste une femme"

        # La présence D'ABORD : SQLite (base par défaut) n'applique pas les
        # ON DELETE CASCADE sans PRAGMA foreign_keys. Supprimer la fiche
        # seule laisserait une présence orpheline, qui continuerait de
        # compter dans les autres tests de ce fichier.
        db.session.delete(presence)
        db.session.delete(jeune)
        db.session.commit()


def test_tableau_actions(app, donnees_senacs):
    with app.app_context():
        from app.services.senacs import tableau_actions

        lignes = {l["atelier"]: l for l in tableau_actions(ANNEE)}
        num = lignes[donnees_senacs["atelier_num"]]
        assert num["seances"] == 1, "la session 2024 ne doit pas compter en 2025"
        assert num["participants_uniques"] == 2
        assert num["participations"] == 2
        assert num["heures_face_public"] == 2.0

        fam = lignes[donnees_senacs["atelier_fam"]]
        assert fam["participations"] == 2
        assert fam["heures_face_public"] == 1.5


def test_page_senacs(admin_client, donnees_senacs):
    r = admin_client.get(f"/bilans/senacs?annee={ANNEE}")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Bilan SENACS" in page
    assert donnees_senacs["atelier_num"] in page
    assert "dédoublonnés" in page


def test_export_senacs_xlsx(admin_client, donnees_senacs):
    r = admin_client.get(f"/bilans/senacs/export.xlsx?annee={ANNEE}")
    assert r.status_code == 200
    assert "spreadsheet" in r.content_type

    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(r.data))
    assert {"Public global", "Actions séances", "Partenariats", "Mode d'emploi"} <= set(wb.sheetnames)
    contenu = "\n".join(str(c.value) for row in wb["Actions séances"].iter_rows() for c in row)
    assert donnees_senacs["atelier_fam"] in contenu


def test_senacs_refuse_anonymes(client):
    assert client.get("/bilans/senacs").status_code == 302
    assert client.get("/bilans/senacs/export.xlsx").status_code == 302
