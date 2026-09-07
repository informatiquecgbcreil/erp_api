"""Module Inscriptions annuelles : saisie, fiche participant, règlement, export.

Le cycle complet d'un bulletin de rentrée :
- saisie du bulletin (coordonnées, ateliers cochés + champ libre, bénévolat
  avec créneaux jour × demi-journée et « je ne sais pas ») ;
- transformation en fiche participant portant le statut d'attente, avec
  inscription automatique aux ateliers choisis ;
- bascule AUTOMATIQUE du statut à la première présence pointée, quelle que
  soit la porte d'entrée (le garde-fou est posé sur la session SQLAlchemy) ;
- confirmation du règlement, avec création de l'adhésion dans le module
  Adhésions & participation ;
- rattachement à une fiche existante plutôt que création d'un doublon ;
- fiche imprimable, export XLSX, cloisonnement par permission.
"""
import uuid
from datetime import date
from io import BytesIO

import pytest

ANNEE = 2031        # campagne « bac à sable », loin des autres jeux de tests
ANNEE_TARIFS = 2032       # campagne AVEC barème dès le départ
ANNEE_BAREME_TARDIF = 2033  # campagne SANS barème au début, complété après coup


def _suffixe():
    return uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def atelier(app):
    """Un atelier actif, cochable sur le bulletin.

    Partagé par tout le fichier : un atelier par test noierait les listes que
    d'autres modules plafonnent (la page d'étiquetage Transitions n'en affiche
    que 200). Sans jauge, pour que les inscriptions restent « inscrit » quel
    que soit le nombre de tests joués."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite

        a = AtelierActivite(nom=f"Atelier numérique {suf}", secteur="Adultes")
        db.session.add(a)
        db.session.commit()
        return {"id": a.id, "nom": a.nom}


@pytest.fixture()
def bareme(app):
    """Le barème d'Antoine : 7 € d'adhésion seul, 10 € en famille, 30 € de
    participation par personne. Posé sur une année dédiée pour ne pas tarifer
    les autres tests."""
    with app.app_context():
        from app.extensions import db
        from app.models import TarifBareme

        TarifBareme.query.filter_by(annee_scolaire=ANNEE_TARIFS).delete()
        for type_tarif, montant in (
            ("adhesion_individuelle", 7.0),
            ("adhesion_familiale", 10.0),
            ("participation", 30.0),
        ):
            db.session.add(TarifBareme(
                annee_scolaire=ANNEE_TARIFS, type_tarif=type_tarif,
                montant=montant, date_debut=date(2000, 1, 1),
            ))
        db.session.commit()
    return {"adhesion_individuelle": 7.0, "adhesion_familiale": 10.0, "participation": 30.0}


def _payload_bulletin(suf: str, atelier_id: int, annee: int = ANNEE, **extra):
    data = {
        "annee": annee,
        "nom": f"Rentree{suf}",
        "prenom": "Soline",
        "adresse": "12 rue des Lilas",
        "code_postal": "60100",
        "ville": "Creil",
        "email": f"soline.{suf}@example.org",
        "telephone": "0344000000",
        "secteur_orienteur": "Adultes",
        "ateliers": [str(atelier_id)],
        "ateliers_libre": "Aimerait aussi de la couture le jeudi",
        "commentaire": "Vient sur conseil d'une voisine.",
        "benevolat_souhaite": "1",
        "benevolat_mission": "Accompagnement scolaire, aide aux devoirs",
        "creneaux": ["mardi|matin", "jeudi|apres_midi"],
        "benevolat_dispo_inconnue": "1",
    }
    data.update(extra)
    return data


def _creer_bulletin(admin_client, app, atelier_id, annee: int = ANNEE, **extra):
    suf = _suffixe()
    r = admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_bulletin(suf, atelier_id, annee, **extra),
        follow_redirects=False,
    )
    assert r.status_code == 302, r.data[:400]
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription = (
            InscriptionAnnuelle.query
            .filter(InscriptionAnnuelle.nom == f"Rentree{suf}")
            .order_by(InscriptionAnnuelle.id.desc())
            .first()
        )
        assert inscription is not None
        return inscription.id, suf


# ---------------------------------------------------------------------------
# Saisie du bulletin
# ---------------------------------------------------------------------------

def test_saisie_bulletin_enregistre_tout_le_formulaire(admin_client, app, atelier):
    """Toutes les données récoltées atterrissent en base, créneaux compris."""
    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.annee_scolaire == ANNEE
        assert i.libelle_annee == f"{ANNEE}-{ANNEE + 1}"
        assert i.prenom == "Soline"
        assert i.adresse_complete == "12 rue des Lilas, 60100 Creil"
        assert i.email == f"soline.{suf}@example.org"
        assert i.secteur_orienteur == "Adultes"
        assert i.statut == "saisie"
        assert i.reglement_confirme is False

        # Ateliers : cases cochées + champ libre
        assert [a.id for a in i.ateliers] == [atelier["id"]]
        assert "couture" in i.ateliers_libre

        # Bénévolat : mission libre, créneaux cochés, « je ne sais pas »
        assert i.benevolat_souhaite is True
        assert "devoirs" in i.benevolat_mission
        assert i.benevolat_dispo_inconnue is True
        assert i.creneaux_benevolat == [("mardi", "matin"), ("jeudi", "apres_midi")]
        assert i.a_creneau("mardi", "matin") is True
        assert i.a_creneau("mardi", "apres_midi") is False


def test_formulaires_saffichent(admin_client, app, atelier):
    """Le formulaire se rend aussi bien vide qu'avec un bulletin existant."""
    page = admin_client.get(f"/inscriptions-annuelles/nouvelle?annee={ANNEE}").data.decode("utf-8")
    assert atelier["nom"] in page                    # ateliers groupés par secteur
    assert "Je ne sais pas encore" in page           # grille bénévolat dépliable
    assert f"{ANNEE}-{ANNEE + 1}" in page

    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])
    page = admin_client.get(f"/inscriptions-annuelles/{inscription_id}/modifier").data.decode("utf-8")
    assert f"Rentree{suf}" in page                   # champs pré-remplis
    assert "couture" in page
    assert 'name="creneaux" value="mardi|matin" checked' in page


def test_nom_obligatoire_et_email_verifie(admin_client, app, atelier):
    """Deux garde-fous de saisie : identité minimale et e-mail plausible."""
    r = admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data={"annee": ANNEE, "nom": "", "prenom": "Sans nom"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "obligatoires" in r.data.decode("utf-8")

    suf = _suffixe()
    r = admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_bulletin(suf, atelier["id"], email="pas-un-email"),
        follow_redirects=True,
    )
    assert "invalide" in r.data.decode("utf-8")
    with app.app_context():
        from app.models import InscriptionAnnuelle

        assert InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first() is None


def test_retirer_lenvie_de_benevolat_efface_les_creneaux(admin_client, app, atelier):
    """Une envie retirée ne doit pas laisser la personne dans la grille."""
    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])

    donnees = _payload_bulletin(suf, atelier["id"])
    donnees.pop("benevolat_souhaite")
    donnees.pop("benevolat_dispo_inconnue")
    donnees["nom"] = f"Rentree{suf}"
    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/modifier", data=donnees, follow_redirects=False
    )
    assert r.status_code == 302

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.benevolat_souhaite is False
        assert i.disponibilites == []
        assert i.benevolat_mission is None
        assert i.benevolat_dispo_inconnue is False


# ---------------------------------------------------------------------------
# Transformation en fiche participant
# ---------------------------------------------------------------------------

def test_creation_fiche_participant_avec_statut_dattente(admin_client, app, atelier):
    """La fiche est créée « en attente de 1re participation » et la personne
    est inscrite aux ateliers cochés (module Activité)."""
    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])

    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/creer-participant",
        data={"inscrire_ateliers": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.extensions import db
        from app.models import (
            STATUT_PARTICIPANT_ATTENTE,
            InscriptionActivite,
            InscriptionAnnuelle,
        )

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.statut == "en_attente"
        assert i.participant_id is not None

        p = i.participant
        assert p.statut_inscription == STATUT_PARTICIPANT_ATTENTE
        assert p.attend_premiere_participation is True
        # Les coordonnées du bulletin sont recopiées sur la fiche.
        assert p.ville == "Creil"
        assert p.email == f"soline.{suf}@example.org"
        assert p.created_secteur == "Adultes"
        # Une envie de bénévolat n'est pas un engagement : le drapeau reste à off.
        assert p.est_benevole is False

        # L'atelier coché est devenu une inscription d'activité.
        lignes = InscriptionActivite.query.filter_by(participant_id=p.id).all()
        assert [l.atelier_id for l in lignes] == [atelier["id"]]
        assert lignes[0].statut == "inscrit"


def test_deuxieme_creation_refusee(admin_client, app, atelier):
    """On ne crée pas deux fiches pour le même bulletin."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"])
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})
    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={}, follow_redirects=True
    )
    assert "déjà une fiche participant" in r.data.decode("utf-8")


def test_rattachement_a_une_fiche_existante(admin_client, app, atelier):
    """Un ancien inscrit se rattache à sa fiche : pas de doublon créé."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        ancien = Participant(nom=f"Rentree{suf}", prenom="Soline", telephone="0344999999")
        db.session.add(ancien)
        db.session.commit()
        ancien_id = ancien.id

    r = admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_bulletin(suf, atelier["id"]),
        follow_redirects=False,
    )
    assert r.status_code == 302
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id

    # La fiche ressemblante est proposée sur la page détail.
    page = admin_client.get(f"/inscriptions-annuelles/{inscription_id}").data.decode("utf-8")
    assert "fiches ressemblantes" in page

    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/rattacher",
        data={"participant_id": ancien_id, "inscrire_ateliers": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.extensions import db
        from app.models import STATUT_PARTICIPANT_ATTENTE, InscriptionAnnuelle, Participant

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.participant_id == ancien_id
        assert i.statut == "en_attente"

        p = db.session.get(Participant, ancien_id)
        assert p.statut_inscription == STATUT_PARTICIPANT_ATTENTE
        # Rattachement : on comble les trous sans écraser l'existant.
        assert p.telephone == "0344999999"
        assert p.ville == "Creil"
        assert Participant.query.filter_by(nom=f"Rentree{suf}").count() == 1


# ---------------------------------------------------------------------------
# Première participation : la bascule automatique
# ---------------------------------------------------------------------------

def test_premiere_presence_fait_tomber_le_statut_dattente(admin_client, app, atelier):
    """Pointer la personne présente suffit : aucune action manuelle."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"])
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import (
            STATUT_PARTICIPANT_ACTIF,
            InscriptionAnnuelle,
            PresenceActivite,
            SessionActivite,
        )

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        participant_id = i.participant_id

        seance = SessionActivite(
            atelier_id=atelier["id"], secteur="Adultes", session_type="COLLECTIF",
            date_session=date(ANNEE, 10, 3), heure_debut="14:00", heure_fin="16:00",
            statut="realisee",
        )
        db.session.add(seance)
        db.session.flush()

        # Émargement brut : le garde-fou est posé sur la session SQLAlchemy,
        # donc il s'applique sans que le module ait été appelé.
        db.session.add(PresenceActivite(session_id=seance.id, participant_id=participant_id))
        db.session.commit()

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.statut == "active"
        assert i.premiere_participation_le == date(ANNEE, 10, 3)
        assert i.participant.statut_inscription == STATUT_PARTICIPANT_ACTIF
        assert i.participant.attend_premiere_participation is False


def test_rafraichir_statuts_rattrape_les_presences_hors_application(app, atelier):
    """Filet de sécurité pour les reprises de données : un bulletin resté en
    attente alors que la personne est déjà venue se recale."""
    with app.app_context():
        from app.extensions import db
        from app.models import (
            STATUT_PARTICIPANT_ACTIF,
            STATUT_PARTICIPANT_ATTENTE,
            InscriptionAnnuelle,
            Participant,
            PresenceActivite,
            SessionActivite,
        )
        from app.services.inscriptions_annuelles import rafraichir_statuts

        suf = _suffixe()
        p = Participant(nom=f"Reprise{suf}", prenom="Alex", statut_inscription=STATUT_PARTICIPANT_ATTENTE)
        seance = SessionActivite(
            atelier_id=atelier["id"], secteur="Adultes", session_type="COLLECTIF",
            date_session=date(ANNEE, 11, 7), statut="realisee",
        )
        db.session.add_all([p, seance])
        db.session.flush()

        i = InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=date(ANNEE, 9, 1),
            nom=p.nom, prenom=p.prenom, statut="en_attente", participant_id=p.id,
        )
        db.session.add(i)
        db.session.flush()

        # Présence insérée en SQL pur : le garde-fou ORM ne la voit pas.
        db.session.execute(
            PresenceActivite.__table__.insert().values(session_id=seance.id, participant_id=p.id)
        )
        db.session.commit()

        inscription_id, participant_id = i.id, p.id
        assert rafraichir_statuts(ANNEE) >= 1

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.statut == "active"
        assert i.premiere_participation_le == date(ANNEE, 11, 7)
        assert db.session.get(Participant, participant_id).statut_inscription == STATUT_PARTICIPANT_ACTIF


# ---------------------------------------------------------------------------
# Règlement
# ---------------------------------------------------------------------------

def test_encaissement_sur_bulletin_sans_bareme(admin_client, app, atelier):
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"])

    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/reglement",
        data={"action": "confirmer", "montant": "12,50", "mode": "cheque",
              "date_reglement": f"{ANNEE}-09-15", "reglement_commentaire": "chèque n°42"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_confirme is True
        assert i.reglement_montant == 12.5           # la virgule décimale est acceptée
        assert i.reglement_mode == "cheque"
        assert i.reglement_date == date(ANNEE, 9, 15)
        assert i.reglement_commentaire == "chèque n°42"
        assert i.cotisation_id is None

    # Le règlement se dé-confirme (erreur de saisie à l'accueil).
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/reglement", data={"action": "annuler"})
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_confirme is False
        assert i.reglement_date is None


def test_transformation_genere_adhesion_et_participation(admin_client, app, atelier, bareme):
    """La création de la fiche pose l'adhésion et la participation dans le
    module Adhésions — le règlement se suit là où il compte."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"], annee=ANNEE_TARIFS)
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        cotisations = Cotisation.query.filter_by(
            participant_id=i.participant_id, annee_scolaire=ANNEE_TARIFS
        ).all()
        types = sorted(c.type_cotisation for c in cotisations)
        assert types == ["adhesion_individuelle", "participation"]
        montants = {c.type_cotisation: c.montant_du for c in cotisations}
        assert montants["adhesion_individuelle"] == 7.0
        assert montants["participation"] == 30.0

        # Le bulletin affiche le total dû sans rien recalculer.
        assert i.reglement_du == 37.0
        assert i.reglement_statut == "rien"


def test_encaissement_partiel_puis_solde(admin_client, app, atelier, bareme):
    """Tout, une partie ou rien : les trois états, et la ventilation sur les
    cotisations (adhésion d'abord)."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"], annee=ANNEE_TARIFS)
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    # Acompte de 10 € sur 37 € : l'adhésion (7 €) est soldée, 3 € vont sur la participation.
    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/reglement",
        data={"action": "encaisser", "montant": "10", "mode": "especes",
              "date_reglement": f"{ANNEE_TARIFS}-09-12"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_statut == "partiel"
        assert i.reglement_montant == 10.0
        assert i.reglement_reste == 27.0

        par_type = {
            c.type_cotisation: c
            for c in Cotisation.query.filter_by(participant_id=i.participant_id, annee_scolaire=ANNEE_TARIFS).all()
        }
        assert par_type["adhesion_individuelle"].solde is True
        assert par_type["participation"].montant_regle == 3.0
        assert par_type["participation"].solde is False

    # Bouton « solder » : encaisse exactement le reste.
    admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/reglement",
        data={"action": "encaisser", "solder": "1", "mode": "cheque"},
        follow_redirects=True,
    )
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_statut == "complet"
        assert i.reglement_confirme is True
        assert i.reglement_reste == 0.0


def test_encaissement_avant_la_fiche_est_reporte(admin_client, app, atelier, bareme):
    """L'accueil encaisse avant d'avoir créé la fiche : la somme est notée sur
    le bulletin, puis reportée dans Adhésions à la transformation."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"], annee=ANNEE_TARIFS)

    admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/reglement",
        data={"action": "encaisser", "montant": "20", "mode": "especes"},
        follow_redirects=True,
    )
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_montant == 20.0
        assert i.reglement_statut == "partiel"
        assert i.cotisation_id is None

    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        cotisations = Cotisation.query.filter_by(
            participant_id=i.participant_id, annee_scolaire=ANNEE_TARIFS
        ).all()
        assert round(sum(c.montant_regle for c in cotisations), 2) == 20.0
        assert i.reglement_statut == "partiel"
        assert i.reglement_reste == 17.0


# ---------------------------------------------------------------------------
# Inscription familiale : foyer, membres et coût
# ---------------------------------------------------------------------------

def _payload_famille(suf, atelier_id, membres, annee=ANNEE_TARIFS):
    """Bulletin familial : une ligne de formulaire par membre déclaré."""
    data = _payload_bulletin(suf, atelier_id, annee)
    data["type_inscription"] = "familiale"
    data["date_naissance"] = f"{annee - 40}-04-15"
    data["membre_id"] = [""] * len(membres)
    data["membre_prenom"] = [m[0] for m in membres]
    data["membre_nom"] = [m[1] for m in membres]
    data["membre_date_naissance"] = [m[2] for m in membres]
    data["membre_lien"] = [m[3] for m in membres]
    return data


def test_bulletin_familial_enregistre_les_membres(admin_client, app, atelier, bareme):
    """Autant de membres qu'on veut, avec ou sans filiation indiquée."""
    suf = _suffixe()
    r = admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [
            ("Léa", "", f"{ANNEE_TARIFS - 12}-03-04", "fille"),
            ("Sacha", "", f"{ANNEE_TARIFS - 8}-11-20", ""),
            ("", "", "", ""),  # ligne vide ajoutée puis pas remplie : ignorée
        ]),
        follow_redirects=False,
    )
    assert r.status_code == 302

    with app.app_context():
        from app.models import InscriptionAnnuelle

        i = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first()
        assert i.est_familiale is True
        assert i.nb_personnes == 3                  # l'inscrite + 2 membres
        assert [m.prenom for m in i.membres] == ["Léa", "Sacha"]

        lea, sacha = i.membres
        assert lea.nom_effectif() == f"Rentree{suf}"  # nom repris de l'inscription
        assert lea.nom_complet == f"Léa Rentree{suf}"
        assert lea.lien_filiation == "fille"
        assert lea.date_naissance == date(ANNEE_TARIFS - 12, 3, 4)
        assert sacha.lien_filiation is None           # filiation non indiquée : accepté


def test_cout_individuel_et_familial(app, bareme):
    """Les deux exemples d'Antoine, au centime près.

    Personne seule : 7 € d'adhésion + 30 € de participation = 37 €.
    Mère + enfant  : 10 € d'adhésion + 30 € × 2 = 70 €."""
    with app.app_context():
        from app.services.cotisations import cout_inscription

        seule = cout_inscription(ANNEE_TARIFS, familiale=False, nb_personnes=1,
                                 a_la_date=date(ANNEE_TARIFS, 9, 15))
        assert seule["montant_adhesion"] == 7.0
        assert seule["montant_participation_total"] == 30.0
        assert seule["total"] == 37.0

        famille = cout_inscription(ANNEE_TARIFS, familiale=True, nb_personnes=2,
                                   a_la_date=date(ANNEE_TARIFS, 9, 15))
        assert famille["montant_adhesion"] == 10.0
        assert famille["montant_participation_unitaire"] == 30.0
        assert famille["montant_participation_total"] == 60.0
        assert famille["total"] == 70.0
        assert famille["manquants"] == []


def test_cout_proratise_en_cours_dannee(app, bareme):
    """Une ligne de barème qui démarre en janvier s'applique aux inscriptions
    postérieures — et à elles seules."""
    with app.app_context():
        from app.extensions import db
        from app.models import TarifBareme
        from app.services.cotisations import cout_inscription

        db.session.add(TarifBareme(
            annee_scolaire=ANNEE_TARIFS, type_tarif="participation",
            montant=15.0, date_debut=date(ANNEE_TARIFS + 1, 1, 1),
            commentaire="demi-tarif second semestre",
        ))
        db.session.commit()

        septembre = cout_inscription(ANNEE_TARIFS, familiale=False, nb_personnes=1,
                                     a_la_date=date(ANNEE_TARIFS, 9, 15))
        fevrier = cout_inscription(ANNEE_TARIFS, familiale=False, nb_personnes=1,
                                   a_la_date=date(ANNEE_TARIFS + 1, 2, 3))
        assert septembre["total"] == 37.0
        assert fevrier["montant_participation_unitaire"] == 15.0
        assert fevrier["total"] == 22.0            # l'adhésion, elle, n'a pas bougé

        # Nettoyage : cette ligne ne doit pas dérégler les autres tests tarifés.
        TarifBareme.query.filter_by(
            annee_scolaire=ANNEE_TARIFS, montant=15.0
        ).delete()
        db.session.commit()


def test_cout_sans_bareme_ne_bloque_pas_mais_alerte(app):
    with app.app_context():
        from app.services.cotisations import cout_inscription

        cout = cout_inscription(1999, familiale=True, nb_personnes=3)
        assert cout["total"] == 0.0
        assert set(cout["manquants"]) == {"adhesion_familiale", "participation"}


def test_transformation_familiale_cree_foyer_fiches_et_cotisations(admin_client, app, atelier, bareme):
    """Le cœur de la demande : une inscription familiale nourrit l'existant —
    une fiche par personne, un foyer, une adhésion familiale, une
    participation chacun."""
    suf = _suffixe()
    admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [
            ("Léa", "", f"{ANNEE_TARIFS - 12}-03-04", "fille"),
        ]),
        follow_redirects=False,
    )
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id

    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import STATUT_PARTICIPANT_ATTENTE, Cotisation, InscriptionAnnuelle, Participant

        i = db.session.get(InscriptionAnnuelle, inscription_id)

        # Une fiche par personne, toutes deux en attente de 1re participation.
        mere = db.session.get(Participant, i.participant_id)
        fille = db.session.get(Participant, i.membres[0].participant_id)
        assert fille is not None
        assert fille.prenom == "Léa"
        assert fille.nom == f"Rentree{suf}"
        assert fille.date_naissance == date(ANNEE_TARIFS - 12, 3, 4)
        assert fille.statut_inscription == STATUT_PARTICIPANT_ATTENTE
        # Coordonnées du foyer recopiées, e-mail (personnel) non.
        assert fille.ville == "Creil"
        assert fille.telephone == "0344000000"
        assert fille.email is None

        # Un seul foyer, celui de l'application — pas un doublon parallèle.
        assert mere.foyer_id is not None
        assert fille.foyer_id == mere.foyer_id
        assert i.foyer_id == mere.foyer_id
        assert {m.id for m in mere.foyer.membres} == {mere.id, fille.id}

        # Adhésion familiale portée par le foyer + une participation chacune.
        adhesion = Cotisation.query.filter_by(
            foyer_id=i.foyer_id, annee_scolaire=ANNEE_TARIFS, type_cotisation="adhesion_familiale"
        ).one()
        assert adhesion.montant_du == 10.0
        participations = Cotisation.query.filter(
            Cotisation.annee_scolaire == ANNEE_TARIFS,
            Cotisation.type_cotisation == "participation",
            Cotisation.participant_id.in_([mere.id, fille.id]),
        ).all()
        assert len(participations) == 2
        assert {c.montant_du for c in participations} == {30.0}

        # Total dû = l'exemple d'Antoine.
        assert i.reglement_du == 70.0


def test_versement_familial_couvre_tout_le_foyer(admin_client, app, atelier, bareme):
    """Le foyer paye en une fois : l'état est le même vu de la mère ou de
    l'enfant, puisque l'adhésion familiale les couvre toutes les deux."""
    suf = _suffixe()
    admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [("Léa", "", f"{ANNEE_TARIFS - 12}-03-04", "fille")]),
    )
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/reglement",
        data={"action": "encaisser", "solder": "1", "mode": "carte"},
        follow_redirects=True,
    )

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle
        from app.services.cotisations import etat_reglement_participant

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_statut == "complet"

        mere = i.participant
        fille = db.session.get(type(mere), i.membres[0].participant_id)
        assert etat_reglement_participant(mere, ANNEE_TARIFS)["statut"] == "complet"
        assert etat_reglement_participant(fille, ANNEE_TARIFS)["statut"] == "complet"
        # 10 € d'adhésion + 30 € de participation propre = 40 € vus de la fille.
        assert etat_reglement_participant(fille, ANNEE_TARIFS)["du"] == 40.0


def test_membre_avec_fiche_existante_est_rattache(admin_client, app, atelier, bareme):
    """Un enfant déjà connu (même nom, prénom ET date de naissance) rejoint sa
    fiche : on ne crée pas un second dossier pour la même personne."""
    suf = _suffixe()
    naissance = date(ANNEE_TARIFS - 10, 6, 1)
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        deja = Participant(nom=f"Rentree{suf}", prenom="Noé", date_naissance=naissance)
        db.session.add(deja)
        db.session.commit()
        deja_id = deja.id

    admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [
            ("Noé", f"Rentree{suf}", naissance.isoformat(), "fils"),
        ]),
    )
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id

    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.membres[0].participant_id == deja_id
        assert Participant.query.filter_by(nom=f"Rentree{suf}", prenom="Noé").count() == 1
        assert db.session.get(Participant, deja_id).foyer_id == i.foyer_id


def test_membre_ajoute_apres_coup_recoit_sa_participation(admin_client, app, atelier, bareme):
    """Un membre déclaré après la transformation : le bouton « mettre les
    cotisations à jour » complète sans rien dupliquer."""
    suf = _suffixe()
    admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [("Léa", "", f"{ANNEE_TARIFS - 12}-03-04", "fille")]),
    )
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, InscriptionAnnuelleMembre, Participant
        from app.services.inscriptions_annuelles import (
            _constituer_foyer,
            _creer_fiches_membres,
        )

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        i.membres.append(InscriptionAnnuelleMembre(prenom="Tom", ordre=1))
        db.session.commit()
        _creer_fiches_membres(i, user_id=None, secteur=None, quartier_id=None)
        _constituer_foyer(i)
        db.session.commit()
        nouvelles_fiches = [m.participant_id for m in i.membres]
        assert all(nouvelles_fiches)

    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/cotisations", follow_redirects=True)

    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        ids = [i.participant_id] + [m.participant_id for m in i.membres]
        participations = Cotisation.query.filter(
            Cotisation.annee_scolaire == ANNEE_TARIFS,
            Cotisation.type_cotisation == "participation",
            Cotisation.participant_id.in_(ids),
        ).all()
        assert len(participations) == 3           # une par personne, sans doublon
        # Une seule adhésion familiale malgré la seconde génération.
        assert Cotisation.query.filter_by(
            foyer_id=i.foyer_id, annee_scolaire=ANNEE_TARIFS, type_cotisation="adhesion_familiale"
        ).count() == 1


def test_membre_avec_fiche_survit_a_une_modification(admin_client, app, atelier, bareme):
    """Décocher « familiale » ne fait pas disparaître une personne qui a déjà
    une fiche dans l'application."""
    suf = _suffixe()
    admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [("Léa", "", f"{ANNEE_TARIFS - 12}-03-04", "fille")]),
    )
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    donnees = _payload_bulletin(suf, atelier["id"], ANNEE_TARIFS)
    donnees["nom"] = f"Rentree{suf}"
    donnees["type_inscription"] = "individuelle"
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/modifier", data=donnees)

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.est_familiale is False
        assert len(i.membres) == 1                # la fiche existante est conservée
        assert i.membres[0].participant_id is not None


# ---------------------------------------------------------------------------
# Cycle de vie
# ---------------------------------------------------------------------------

def test_annulation_puis_reactivation(admin_client, app, atelier):
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"])

    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/statut", data={"action": "annuler"})
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        assert db.session.get(InscriptionAnnuelle, inscription_id).statut == "annulee"

    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/statut", data={"action": "reactiver"})
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        assert db.session.get(InscriptionAnnuelle, inscription_id).statut == "saisie"


def test_suppression_refusee_quand_une_fiche_en_depend(admin_client, app, atelier):
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"])
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/supprimer", follow_redirects=True
    )
    assert "annule-le plutôt" in r.data.decode("utf-8")
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        assert db.session.get(InscriptionAnnuelle, inscription_id) is not None


def test_suppression_dun_bulletin_saisi_en_double(admin_client, app, atelier):
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"])
    r = admin_client.post(f"/inscriptions-annuelles/{inscription_id}/supprimer", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        assert db.session.get(InscriptionAnnuelle, inscription_id) is None


# ---------------------------------------------------------------------------
# Pages, fiche imprimable et export
# ---------------------------------------------------------------------------

def test_liste_et_compteurs(admin_client, app, atelier):
    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])
    page = admin_client.get(f"/inscriptions-annuelles/?annee={ANNEE}").data.decode("utf-8")
    assert f"Rentree{suf}" in page
    assert f"{ANNEE}-{ANNEE + 1}" in page
    assert "Disponibilités bénévolat" in page

    # Le filtre « bénévolat » retrouve la personne, le filtre « réglé » non.
    assert f"Rentree{suf}" in admin_client.get(
        f"/inscriptions-annuelles/?annee={ANNEE}&benevolat=1"
    ).data.decode("utf-8")
    assert f"Rentree{suf}" not in admin_client.get(
        f"/inscriptions-annuelles/?annee={ANNEE}&reglement=complet"
    ).data.decode("utf-8")
    assert f"Rentree{suf}" in admin_client.get(
        f"/inscriptions-annuelles/?annee={ANNEE}&reglement=a_regler"
    ).data.decode("utf-8")


def test_fiche_imprimable(admin_client, app, atelier):
    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])
    page = admin_client.get(f"/inscriptions-annuelles/{inscription_id}/fiche").data.decode("utf-8")
    assert f"Rentree{suf}" in page
    assert "Fiche d'inscription" in page
    assert "réservée à l'accueil" in page
    assert atelier["nom"] in page
    assert "window.print()" in page


def test_export_xlsx_contient_toutes_les_donnees(admin_client, app, atelier):
    from openpyxl import load_workbook

    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])
    r = admin_client.get(f"/inscriptions-annuelles/export.xlsx?annee={ANNEE}")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["Content-Type"]
    assert f"inscriptions_{ANNEE}-{ANNEE + 1}.xlsx" in r.headers["Content-Disposition"]

    wb = load_workbook(BytesIO(r.data))
    assert wb.sheetnames == ["Inscriptions", "Synthèse", "Foyers", "Bénévolat"]

    detail = wb["Inscriptions"]
    entetes = [c.value for c in detail[4]]
    for colonne in ("Nom", "Prénom", "Adresse", "E-mail", "Téléphone",
                    "Secteur qui fait venir", "Ateliers choisis",
                    "Autres souhaits (champ libre)", "Bénévolat souhaité",
                    "Bénévolat — pour quoi faire", "Bénévolat — disponibilités",
                    "Type d'inscription", "Personnes couvertes", "Membres du foyer",
                    "Adhésion (€)", "Participation unitaire (€)", "Total dû (€)",
                    "Montant réglé (€)", "Reste dû (€)", "État du règlement"):
        assert colonne in entetes

    lignes = [[c.value for c in ligne] for ligne in detail.iter_rows(min_row=5)]
    ligne = next(l for l in lignes if l[entetes.index("Nom")] == f"Rentree{suf}")
    assert ligne[entetes.index("Ateliers choisis")] == atelier["nom"]
    assert "couture" in ligne[entetes.index("Autres souhaits (champ libre)")]
    assert ligne[entetes.index("Bénévolat souhaité")] == "Oui"
    assert "Mardi matin" in ligne[entetes.index("Bénévolat — disponibilités")]
    assert ligne[entetes.index("État du règlement")] == "Non réglé"
    assert ligne[entetes.index("Type d'inscription")] == "Inscription individuelle"
    assert ligne[entetes.index("Personnes couvertes")] == 1

    # La feuille bénévolat sert la réunion d'équipe : grille + liste nominative.
    benevolat = [[c.value for c in ligne] for ligne in wb["Bénévolat"].iter_rows()]
    assert any(f"Soline Rentree{suf}" == (l[0] or "") for l in benevolat)


# ---------------------------------------------------------------------------
# Barème complété APRÈS la transformation : le cas remonté par Antoine.
#
# Transformer une inscription avant d'avoir rempli le barème des tarifs crée
# des cotisations à 0 €. Ce n'est pas une erreur bloquante (l'accueil ne doit
# jamais rester coincé), mais il faut que « Mettre les cotisations à jour »
# rattrape vraiment ces montants une fois le barème complété — et que la
# fiche ne prétende jamais que « rien à devoir » veut dire « déjà réglé ».
# ---------------------------------------------------------------------------

def test_transformation_sans_bareme_ne_pretend_pas_que_cest_regle(admin_client, app, atelier):
    """Sans barème, les cotisations naissent à 0 € : la fiche doit le dire
    clairement, pas afficher un « Tout est réglé » trompeur."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"], annee=ANNEE_BAREME_TARDIF)
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        cotisations = Cotisation.query.filter_by(
            participant_id=i.participant_id, annee_scolaire=ANNEE_BAREME_TARDIF
        ).all()
        assert len(cotisations) == 2
        assert {c.montant_du for c in cotisations} == {0.0}
        assert i.reglement_du == 0.0
        # « rien réglé », jamais « complet » : sans quoi le formulaire de
        # paiement se cache derrière un badge vert mensonger.
        assert i.reglement_statut == "rien"

    page = admin_client.get(f"/inscriptions-annuelles/{inscription_id}").data.decode("utf-8")
    assert "Rien n'est encore dû" in page
    assert "à chiffrer" in page
    assert "Tout est réglé" not in page
    assert 'name="montant"' not in page          # pas de formulaire pour un montant inconnu
    assert "Mettre les cotisations à jour" in page

    # La fiche imprimable ne doit jamais prétendre que c'est réglé non plus.
    fiche = admin_client.get(f"/inscriptions-annuelles/{inscription_id}/fiche").data.decode("utf-8")
    assert "Barème incomplet" in fiche
    assert "Intégralement réglé" not in fiche or "☒ Intégralement réglé" not in fiche


def test_completer_le_bareme_apres_coup_corrige_les_cotisations_a_zero(admin_client, app, atelier):
    """Le cœur du bug : « Mettre à jour » doit rattraper les montants nés à
    0 €, pas les laisser figés pour toujours."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"], annee=ANNEE_BAREME_TARDIF)
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    # Le barème est complété seulement maintenant.
    with app.app_context():
        from app.extensions import db
        from app.models import TarifBareme

        for type_tarif, montant in (
            ("adhesion_individuelle", 7.0), ("adhesion_familiale", 10.0), ("participation", 30.0),
        ):
            db.session.add(TarifBareme(
                annee_scolaire=ANNEE_BAREME_TARDIF, type_tarif=type_tarif,
                montant=montant, date_debut=date(2000, 1, 1),
            ))
        db.session.commit()

    r = admin_client.post(
        f"/inscriptions-annuelles/{inscription_id}/cotisations", follow_redirects=True
    )
    assert "rattrapé" in r.data.decode("utf-8")

    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        cotisations = {
            c.type_cotisation: c.montant_du
            for c in Cotisation.query.filter_by(
                participant_id=i.participant_id, annee_scolaire=ANNEE_BAREME_TARDIF
            ).all()
        }
        # Les DEUX lignes existantes sont corrigées — aucun doublon créé.
        assert cotisations == {"adhesion_individuelle": 7.0, "participation": 30.0}
        assert i.reglement_du == 37.0
        assert i.reglement_statut == "rien"       # chiffré maintenant, mais toujours pas payé
        assert i.reglement_reste == 37.0

    # Le formulaire d'encaissement est enfin là.
    page = admin_client.get(f"/inscriptions-annuelles/{inscription_id}").data.decode("utf-8")
    assert 'name="montant"' in page
    assert "37.00" in page
    assert "Rien n&#39;est encore dû" not in page


def test_rattrapage_epargne_un_montant_deja_verse(app):
    """Une fois qu'un euro a été versé sur une cotisation, son montant devient
    une dette réelle : le rattrapage automatique ne doit plus jamais y toucher,
    même si une saisie manuelle l'avait laissée à 0 €."""
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, Paiement, Participant
        from app.services.inscriptions_annuelles import _rattraper_montant_placeholder

        p = Participant(nom=f"Garde{_suffixe()}", prenom="Fou")
        db.session.add(p)
        db.session.flush()

        c = Cotisation(
            annee_scolaire=1998, type_cotisation="participation",
            participant_id=p.id, montant_du=0.0, date_reference=date.today(),
        )
        db.session.add(c)
        db.session.flush()

        # Jamais versée : le rattrapage joue son rôle normalement.
        assert _rattraper_montant_placeholder(c, 30.0) is True
        assert c.montant_du == 30.0

        # Un versement existe désormais : plus aucun rattrapage, quel que
        # soit le montant_du (même remis manuellement à 0).
        c.montant_du = 0.0
        c.paiements.append(Paiement(montant=5.0, date_paiement=date.today(), mode="especes"))
        db.session.commit()
        assert _rattraper_montant_placeholder(c, 30.0) is False
        assert c.montant_du == 0.0


def test_rattrapage_epargne_un_montant_reduit_manuellement(app):
    """Un montant non nul (même un tarif réduit négocié à la main) n'est
    jamais considéré comme un placeholder : on ne l'écrase pas."""
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, Participant
        from app.services.inscriptions_annuelles import _rattraper_montant_placeholder

        p = Participant(nom=f"Reduit{_suffixe()}", prenom="Precaire")
        db.session.add(p)
        db.session.flush()
        c = Cotisation(
            annee_scolaire=1998, type_cotisation="participation",
            participant_id=p.id, montant_du=3.0, date_reference=date.today(),
        )
        db.session.add(c)
        db.session.commit()

        assert _rattraper_montant_placeholder(c, 30.0) is False
        assert c.montant_du == 3.0


# ---------------------------------------------------------------------------
# L'état de règlement s'affiche là où l'accueil regarde
# ---------------------------------------------------------------------------

@pytest.fixture()
def personne_a_jour_ou_pas(app):
    """Une personne avec une dette connue sur l'année scolaire EN COURS.

    Les badges parlent toujours de l'année en cours — c'est ce que l'accueil
    a besoin de savoir. On pose donc la cotisation directement, sans dépendre
    du barème que d'autres tests peuvent avoir renseigné."""
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, Participant
        from app.services.cotisations import annee_scolaire_courante

        suf = _suffixe()
        p = Participant(nom=f"Badge{suf}", prenom="Camille",
                        statut_inscription="attente_premiere_participation")
        db.session.add(p)
        db.session.flush()
        cotisation = Cotisation(
            annee_scolaire=annee_scolaire_courante(),
            type_cotisation="participation",
            participant_id=p.id,
            montant_du=40.0,
            date_reference=date.today(),
        )
        db.session.add(cotisation)
        db.session.commit()
        return {"participant_id": p.id, "cotisation_id": cotisation.id, "suf": suf}


def _verser(app, cotisation_id, montant):
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation
        from app.services.cotisations import repartir_versement

        repartir_versement([db.session.get(Cotisation, cotisation_id)], montant)
        db.session.commit()


def test_badge_reglement_sur_la_fiche_participant(admin_client, app, personne_a_jour_ou_pas):
    """« Non réglé / partiel / à jour » doit sauter aux yeux sur la fiche."""
    pid = personne_a_jour_ou_pas["participant_id"]

    page = admin_client.get(f"/participants/{pid}/synthese").data.decode("utf-8")
    assert "Non réglé" in page
    assert "40.00 €" in page
    # La fiche vient d'une inscription annuelle : le statut d'attente s'affiche aussi.
    assert "en attente de 1" in page.lower()

    _verser(app, personne_a_jour_ou_pas["cotisation_id"], 15.0)
    page = admin_client.get(f"/participants/{pid}/synthese").data.decode("utf-8")
    assert "Partiellement réglé" in page
    assert "25.00 €" in page

    _verser(app, personne_a_jour_ou_pas["cotisation_id"], 25.0)
    page = admin_client.get(f"/participants/{pid}/synthese").data.decode("utf-8")
    assert "À jour" in page


def test_badge_reglement_a_lemargement(admin_client, app, atelier, personne_a_jour_ou_pas):
    """Sur la feuille d'émargement, l'accueil voit qui doit encore payer."""
    pid = personne_a_jour_ou_pas["participant_id"]
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite, SessionActivite

        seance = SessionActivite(
            atelier_id=atelier["id"], secteur="Adultes", session_type="COLLECTIF",
            date_session=date.today(), heure_debut="14:00", heure_fin="16:00",
            statut="realisee",
        )
        db.session.add(seance)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=seance.id, participant_id=pid))
        db.session.commit()
        session_id = seance.id

    page = admin_client.get(f"/activite/session/{session_id}/emargement").data.decode("utf-8")
    assert f"Badge{personne_a_jour_ou_pas['suf']}" in page
    assert "regl-badge" in page
    assert "Non réglé" in page
    assert "40.00 €" in page

    _verser(app, personne_a_jour_ou_pas["cotisation_id"], 40.0)
    page = admin_client.get(f"/activite/session/{session_id}/emargement").data.decode("utf-8")
    assert "À jour" in page
    assert "Non réglé" not in page


def test_versement_saisi_depuis_la_fiche_remonte_dans_la_liste(admin_client, app, atelier, bareme):
    """L'accueil encaisse depuis la fiche participant : la liste des
    inscriptions doit le refléter sans qu'on y touche."""
    inscription_id, _ = _creer_bulletin(admin_client, app, atelier["id"], annee=ANNEE_TARIFS)
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        participant_id = i.participant_id
        cotisation_id = i.cotisation_id

    admin_client.post(
        f"/participants/{participant_id}/cotisation/{cotisation_id}/versement",
        data={"montant": "7", "mode": "especes"},
    )
    # L'ouverture de la liste resynchronise les bulletins.
    admin_client.get(f"/inscriptions-annuelles/?annee={ANNEE_TARIFS}")

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.reglement_statut == "partiel"
        assert i.reglement_montant == 7.0
        assert i.reglement_reste == 30.0


# ---------------------------------------------------------------------------
# Droits
# ---------------------------------------------------------------------------

def test_pages_protegees(client):
    """Un visiteur anonyme est renvoyé vers la connexion, jamais servi."""
    for url in ("/inscriptions-annuelles/", "/inscriptions-annuelles/nouvelle",
                "/inscriptions-annuelles/export.xlsx"):
        r = client.get(url)
        assert r.status_code in (302, 401), url


def test_permissions_declarees_et_attribuees(app):
    with app.app_context():
        from app.models import Permission, Role

        codes = {p.code for p in Permission.query.all()}
        attendues = {
            "inscriptions_annuelles:view",
            "inscriptions_annuelles:edit",
            "inscriptions_annuelles:reglement",
            "inscriptions_annuelles:export",
        }
        assert attendues <= codes

        direction = {p.code for p in Role.query.filter_by(code="direction").first().permissions}
        assert attendues <= direction

        responsable = {p.code for p in Role.query.filter_by(code="responsable_secteur").first().permissions}
        assert attendues <= responsable

        # Le compte technique consulte mais ne saisit pas.
        tech = {p.code for p in Role.query.filter_by(code="admin_tech").first().permissions}
        assert "inscriptions_annuelles:view" in tech
        assert "inscriptions_annuelles:edit" not in tech


def test_hub_publics_propose_le_module(admin_client):
    page = admin_client.get("/publics").data.decode("utf-8")
    assert "Inscriptions annuelles" in page
    assert "/inscriptions-annuelles/" in page


# ---------------------------------------------------------------------------
# RGPD : le bulletin recopie des données personnelles, il suit la fiche
# ---------------------------------------------------------------------------

def test_anonymisation_efface_aussi_le_bulletin(admin_client, app, atelier):
    """Anonymiser la fiche sans toucher au bulletin ne serait pas anonymiser."""
    inscription_id, suf = _creer_bulletin(admin_client, app, atelier["id"])
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle
        from app.services.purge_rgpd import NOM_ANONYME, anonymiser_participant

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        anonymiser_participant(i.participant)
        db.session.commit()

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.nom == NOM_ANONYME
        assert f"Rentree{suf}" != i.nom
        assert i.adresse is None and i.code_postal is None and i.ville is None
        assert i.email is None and i.telephone is None
        assert i.commentaire is None
        # Ce qui décrit la demande survit : les bilans de campagne restent justes.
        assert [a.id for a in i.ateliers] == [atelier["id"]]
        assert i.benevolat_souhaite is True
        assert i.creneaux_benevolat == [("mardi", "matin"), ("jeudi", "apres_midi")]


def test_anonymisation_efface_aussi_les_membres_du_foyer(admin_client, app, atelier, bareme):
    """Un enfant déclaré sur le bulletin est une personne : son identité doit
    partir elle aussi, sinon on n'a rien anonymisé."""
    suf = _suffixe()
    admin_client.post(
        "/inscriptions-annuelles/nouvelle",
        data=_payload_famille(suf, atelier["id"], [
            ("Léa", "", f"{ANNEE_TARIFS - 12}-03-04", "fille"),
        ]),
    )
    with app.app_context():
        from app.models import InscriptionAnnuelle

        inscription_id = InscriptionAnnuelle.query.filter_by(nom=f"Rentree{suf}").first().id
    admin_client.post(f"/inscriptions-annuelles/{inscription_id}/creer-participant", data={})

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.purge_rgpd import NOM_ANONYME, anonymiser_participant

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        fille_id = i.membres[0].participant_id

        # Anonymiser la mère laisse la fille intacte : ce sont deux personnes.
        anonymiser_participant(i.participant)
        db.session.commit()
        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.nom == NOM_ANONYME
        assert i.membres[0].prenom == "Léa"

        # Anonymiser la fille efface sa ligne sur le bulletin de sa mère.
        anonymiser_participant(db.session.get(Participant, fille_id))
        db.session.commit()
        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.membres[0].nom == NOM_ANONYME
        assert i.membres[0].prenom != "Léa"
        assert i.membres[0].date_naissance is None
        assert i.membres[0].lien_filiation is None


def test_anonymisation_efface_un_membre_sans_fiche(app, atelier):
    """Un membre jamais transformé n'est connu que par ce bulletin : il part
    avec l'anonymisation de l'inscrit·e principal·e."""
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, InscriptionAnnuelleMembre, Participant
        from app.services.purge_rgpd import NOM_ANONYME, anonymiser_participant

        suf = _suffixe()
        p = Participant(nom=f"Seul{suf}", prenom="Dominique")
        db.session.add(p)
        db.session.flush()
        i = InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=date(ANNEE, 9, 1),
            nom=p.nom, prenom=p.prenom, participant_id=p.id,
            type_inscription="familiale",
        )
        i.membres.append(InscriptionAnnuelleMembre(
            prenom="Jules", nom=f"Seul{suf}", date_naissance=date(ANNEE - 9, 2, 2),
            lien_filiation="fils",
        ))
        db.session.add(i)
        db.session.commit()
        inscription_id = i.id

        anonymiser_participant(db.session.get(Participant, p.id))
        db.session.commit()

        i = db.session.get(InscriptionAnnuelle, inscription_id)
        assert i.membres[0].nom == NOM_ANONYME
        assert i.membres[0].prenom != "Jules"
        assert i.membres[0].date_naissance is None


def test_une_inscription_recente_protege_de_la_purge(app, atelier):
    """Inscrit à la rentrée, jamais encore venu : la purge ne doit pas
    l'anonymiser sous prétexte qu'il n'a aucune présence."""
    from datetime import date as _date

    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.purge_rgpd import derniere_activite_par_participant

        suf = _suffixe()
        p = Participant(nom=f"Nouveau{suf}", prenom="Sam")
        db.session.add(p)
        db.session.flush()
        db.session.add(InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=_date.today(),
            nom=p.nom, prenom=p.prenom, participant_id=p.id, statut="en_attente",
        ))
        db.session.commit()

        activites = derniere_activite_par_participant()
        assert p.id in activites, "une inscription annuelle doit valoir activité"


def test_suppression_definitive_emporte_le_bulletin(app, atelier):
    """La suppression totale d'une fiche ne doit rien laisser derrière elle —
    créneaux de bénévolat et ateliers souhaités compris."""
    with app.app_context():
        from app.extensions import db
        from app.models import (
            InscriptionAnnuelle,
            InscriptionAnnuelleDispo,
            Participant,
            inscription_annuelle_atelier,
        )
        from app.services.inscriptions_annuelles import appliquer_disponibilites
        from app.services.participant_suppression import analyser, supprimer_definitivement

        suf = _suffixe()
        p = Participant(nom=f"Efface{suf}", prenom="Dominique")
        db.session.add(p)
        db.session.flush()

        i = InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=date(ANNEE, 9, 1),
            nom=p.nom, prenom=p.prenom, participant_id=p.id,
            benevolat_souhaite=True,
        )
        i.ateliers = [db.session.get(type(i).ateliers.property.mapper.class_, atelier["id"])]
        db.session.add(i)
        appliquer_disponibilites(i, ["lundi|matin", "vendredi|apres_midi"])
        db.session.commit()
        inscription_id, participant_id = i.id, p.id

        # Le bulletin est annoncé avant la suppression.
        resume = analyser(db.session.get(Participant, participant_id))
        assert any("inscription annuelle" in libelle for libelle, _ in resume["details"])

        supprimer_definitivement(db.session.get(Participant, participant_id))
        db.session.commit()

        assert db.session.get(Participant, participant_id) is None
        assert db.session.get(InscriptionAnnuelle, inscription_id) is None
        assert InscriptionAnnuelleDispo.query.filter_by(inscription_id=inscription_id).count() == 0
        restes = db.session.execute(
            inscription_annuelle_atelier.select().where(
                inscription_annuelle_atelier.c.inscription_id == inscription_id
            )
        ).all()
        assert restes == []


def test_export_rgpd_contient_les_inscriptions(app, atelier):
    """Droit d'accès (article 15) : la personne doit recevoir son bulletin."""
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant
        from app.services.rgpd_export import construire_export_rgpd

        suf = _suffixe()
        p = Participant(nom=f"Acces{suf}", prenom="Noa")
        db.session.add(p)
        db.session.flush()
        db.session.add(InscriptionAnnuelle(
            annee_scolaire=ANNEE, date_inscription=date(ANNEE, 9, 4),
            nom=p.nom, prenom=p.prenom, participant_id=p.id,
            telephone="0344123456", secteur_orienteur="Adultes",
            ateliers_libre="Cours de français", benevolat_souhaite=True,
            benevolat_mission="Sorties familles",
        ))
        db.session.commit()

        wb = construire_export_rgpd(db.session.get(Participant, p.id))
        assert "Inscriptions annuelles" in wb.sheetnames
        feuille = wb["Inscriptions annuelles"]
        lignes = [[c.value for c in ligne] for ligne in feuille.iter_rows(min_row=2)]
        assert any("Cours de français" in (c or "") for ligne in lignes for c in ligne)
        assert any("Sorties familles" in (c or "") for ligne in lignes for c in ligne)
