"""« Elle est inscrite, celle-là ? elle a payé ? »

C'est la question de l'accueil, posée pendant que la personne attend devant
le bureau. Les trois réponses vivaient dans trois modules différents :
l'inscription à l'atelier (module Activités), le bulletin d'inscription
annuelle (module Inscriptions), le règlement (module Adhésions). Il fallait
ouvrir trois écrans pour répondre à une question de cinq secondes.

L'écran d'émargement — celui de l'application, pas la feuille imprimée —
les rassemble maintenant sur la ligne de chaque personne.

Deux règles de métier tiennent tout le reste :

1. **L'année de référence est celle de la SÉANCE.** Rouvrir en septembre
   l'émargement d'une séance de juin doit montrer la situation de juin. La
   situation d'aujourd'hui n'apprend rien sur ce qui s'est passé ce jour-là.

2. **Les trois pastilles sont TOUJOURS affichées**, y compris « aucune
   cotisation » et « sans inscription ». Une pastille qui n'apparaît qu'en
   cas de problème laisse, quand elle manque, le doute entre « tout va
   bien » et « l'information n'a pas été calculée ». À l'accueil, ce doute
   coûte le même temps que d'aller voir dans l'autre module.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


@pytest.fixture()
def seance(app):
    """Une séance de juin 2026 (année scolaire 2025) et quatre profils.

    - ``inscrite`` : inscrite à l'atelier, bulletin de l'année, tout réglé ;
    - ``attente``  : sur la liste d'attente, pas de bulletin, rien réglé ;
    - ``libre``    : venue sans rien — accueil libre, cas parfaitement
      légitime, mais qu'il faut savoir ;
    - ``a_pointer``: inscrite, pas encore pointée (bloc « à pointer »).
    """
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import (
            AtelierActivite, Cotisation, InscriptionActivite, InscriptionAnnuelle,
            Paiement, Participant, PresenceActivite, SessionActivite,
        )

        atelier = AtelierActivite(secteur="Adultes", nom=f"Situ{suf}")
        db.session.add(atelier)
        db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur="Adultes", session_type="COLLECTIF",
                            date_session=date(2026, 6, 9), heure_debut="14:00", heure_fin="16:00")
        db.session.add(s)
        db.session.flush()

        inscrite = Participant(nom=f"Adam{suf}", prenom="Nora")
        attente = Participant(nom=f"Bec{suf}", prenom="Yanis")
        libre = Participant(nom=f"Cros{suf}", prenom="Lila")
        a_pointer = Participant(nom=f"Dumas{suf}", prenom="Hugo")
        db.session.add_all([inscrite, attente, libre, a_pointer])
        db.session.flush()

        db.session.add_all([
            PresenceActivite(session_id=s.id, participant_id=inscrite.id),
            PresenceActivite(session_id=s.id, participant_id=attente.id),
            PresenceActivite(session_id=s.id, participant_id=libre.id),
        ])
        db.session.add_all([
            InscriptionActivite(atelier_id=atelier.id, participant_id=inscrite.id, statut="inscrit"),
            InscriptionActivite(atelier_id=atelier.id, participant_id=attente.id, statut="attente"),
            InscriptionActivite(atelier_id=atelier.id, participant_id=a_pointer.id, statut="inscrit"),
        ])

        # Bulletin de l'année scolaire de la séance (juin 2026 -> 2025-2026).
        db.session.add(InscriptionAnnuelle(
            annee_scolaire=2025, date_inscription=date(2025, 9, 15),
            nom=inscrite.nom, prenom=inscrite.prenom, participant_id=inscrite.id))

        # Nora a tout réglé ; Yanis doit 20 € et n'a rien versé.
        reglee = Cotisation(annee_scolaire=2025, type_cotisation="adhesion_individuelle",
                            participant_id=inscrite.id, montant_du=20.0,
                            date_reference=date(2025, 9, 15))
        impayee = Cotisation(annee_scolaire=2025, type_cotisation="adhesion_individuelle",
                             participant_id=attente.id, montant_du=20.0,
                             date_reference=date(2025, 9, 15))
        db.session.add_all([reglee, impayee])
        db.session.flush()
        db.session.add(Paiement(cotisation_id=reglee.id, montant=20.0,
                                date_paiement=date(2025, 9, 15), mode="especes"))
        db.session.commit()

        contexte = {
            "session_id": s.id, "atelier_id": atelier.id,
            "inscrite_id": inscrite.id, "attente_id": attente.id,
            "libre_id": libre.id, "a_pointer_id": a_pointer.id,
            "nom_libre": libre.nom, "nom_a_pointer": a_pointer.nom,
        }

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import (
            ArchiveEmargement, AtelierActivite, InscriptionActivite,
            InscriptionAnnuelle, Participant, PresenceActivite,
        )

        # On démonte à la main ce que la base ne démonte pas pour nous :
        # SQLite n'applique pas ON DELETE CASCADE, et il RECYCLE les
        # identifiants. Un atelier supprimé en laissant ses inscriptions
        # derrière lui les lègue au prochain atelier créé — qui hérite du
        # même identifiant, et voit double.
        for archive in ArchiveEmargement.query.filter_by(atelier_id=contexte["atelier_id"]).all():
            db.session.delete(archive)
        for presence in PresenceActivite.query.filter_by(session_id=contexte["session_id"]).all():
            db.session.delete(presence)
        for ligne in InscriptionActivite.query.filter_by(atelier_id=contexte["atelier_id"]).all():
            db.session.delete(ligne)
        db.session.flush()
        a = db.session.get(AtelierActivite, contexte["atelier_id"])
        if a is not None:
            db.session.delete(a)
        for ins in InscriptionAnnuelle.query.filter_by(annee_scolaire=2025).all():
            if ins.participant_id in (contexte["inscrite_id"],):
                db.session.delete(ins)
        for cle in ("inscrite_id", "attente_id", "libre_id", "a_pointer_id"):
            p = db.session.get(Participant, contexte[cle])
            if p is not None:
                db.session.delete(p)
        db.session.commit()


def _situations(app, contexte):
    from app.extensions import db
    from app.models import SessionActivite
    from app.services.emargement import situations

    s = db.session.get(SessionActivite, contexte["session_id"])
    return situations(s, [contexte["inscrite_id"], contexte["attente_id"],
                          contexte["libre_id"], contexte["a_pointer_id"]])


# ---------------------------------------------------------------------------
# Le service
# ---------------------------------------------------------------------------

def test_inscription_a_latelier(app, seance):
    with app.app_context():
        situ = _situations(app, seance)
        assert situ[seance["inscrite_id"]]["atelier"] == "inscrit"
        assert situ[seance["attente_id"]]["atelier"] == "attente"
        # Venue sans inscription préalable : légitime, mais on le dit.
        assert situ[seance["libre_id"]]["atelier"] == "aucune"


def test_une_inscription_annulee_ne_compte_pas(app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionActivite

        ligne = InscriptionActivite.query.filter_by(
            atelier_id=seance["atelier_id"], participant_id=seance["inscrite_id"]).one()
        ligne.statut = "annule"
        db.session.commit()
        assert _situations(app, seance)[seance["inscrite_id"]]["atelier"] == "aucune"


def test_inscrit_lemporte_sur_liste_dattente(app, seance):
    """Inscrit à l'atelier ET en attente sur une séance : la personne est inscrite.

    Sinon l'ordre des lignes en base déciderait de ce qu'on affiche.
    """
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionActivite

        db.session.add(InscriptionActivite(
            atelier_id=seance["atelier_id"], session_id=seance["session_id"],
            participant_id=seance["inscrite_id"], statut="attente"))
        db.session.commit()
        assert _situations(app, seance)[seance["inscrite_id"]]["atelier"] == "inscrit"


def test_bulletin_de_lannee(app, seance):
    with app.app_context():
        situ = _situations(app, seance)
        assert situ[seance["inscrite_id"]]["bulletin"] is True
        assert situ[seance["attente_id"]]["bulletin"] is False


def test_bulletin_rattache_par_le_nom_si_la_fiche_na_pas_didentifiant(app, seance):
    """Le module d'inscription est récent : beaucoup de bulletins ont été
    saisis avant que la fiche participant existe, et ne portent pas encore
    son identifiant. Les ignorer afficherait « pas de bulletin » à des gens
    qui en ont bien rempli un."""
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle, Participant

        p = db.session.get(Participant, seance["attente_id"])
        db.session.add(InscriptionAnnuelle(
            annee_scolaire=2025, date_inscription=date(2025, 9, 20),
            nom=p.nom.upper(), prenom=p.prenom.lower(), participant_id=None))
        db.session.commit()
        assert _situations(app, seance)[seance["attente_id"]]["bulletin"] is True


def test_lannee_est_celle_de_la_seance_pas_celle_daujourdhui(app, seance):
    """Juin 2026 appartient à l'année scolaire 2025-2026, pas à l'année en cours."""
    with app.app_context():
        situ = _situations(app, seance)
        assert situ[seance["inscrite_id"]]["annee_scolaire"] == 2025
        assert situ[seance["inscrite_id"]]["libelle_annee"] == "2025-2026"


def test_un_bulletin_dune_autre_annee_ne_compte_pas(app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import InscriptionAnnuelle

        db.session.add(InscriptionAnnuelle(
            annee_scolaire=2026, date_inscription=date(2026, 9, 10),
            nom="Peu", prenom="Importe", participant_id=seance["attente_id"]))
        db.session.commit()
        assert _situations(app, seance)[seance["attente_id"]]["bulletin"] is False


def test_etat_de_reglement(app, seance):
    with app.app_context():
        situ = _situations(app, seance)
        assert situ[seance["inscrite_id"]]["reglement"]["statut"] == "complet"
        assert situ[seance["attente_id"]]["reglement"]["statut"] == "rien"
        # Le cas le plus fréquent, et celui qu'on masquait : aucune
        # cotisation du tout. « Je ne sais pas » n'est pas « à jour ».
        assert situ[seance["libre_id"]]["reglement"]["statut"] == "aucune"


def test_sans_personne_aucune_requete(app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.emargement import situations

        s = db.session.get(SessionActivite, seance["session_id"])
        assert situations(s, []) == {}
        assert situations(s, [None]) == {}


# ---------------------------------------------------------------------------
# L'écran
# ---------------------------------------------------------------------------

def _page(admin_client, seance):
    r = admin_client.get(f"/activite/session/{seance['session_id']}/emargement")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_lecran_montre_les_trois_reponses(admin_client, seance):
    page = _page(admin_client, seance)
    assert "Inscrit·e" in page
    assert "Liste d'attente" in page
    assert "Sans inscription" in page
    assert "Pas de bulletin" in page
    assert "Bulletin" in page


def test_lecran_affiche_aucune_cotisation(admin_client, seance):
    """Ce libellé était calculé mais masqué : c'est justement le cas qui
    envoie l'accueil ouvrir un autre module."""
    page = _page(admin_client, seance)
    assert "Aucune cotisation" in page
    assert "Non réglé" in page
    assert "À jour" in page


def test_lecran_montre_lannee_de_la_seance(admin_client, seance):
    assert "2025-2026" in _page(admin_client, seance)


def test_le_bloc_a_pointer_porte_la_meme_information(admin_client, seance):
    """Décider de pointer quelqu'un sans voir sa situation, c'est devoir
    revenir sur sa ligne juste après pour la lire."""
    page = _page(admin_client, seance)
    debut = page.index(seance["nom_a_pointer"])
    extrait = page[debut:debut + 1600]
    assert "situ-ligne" in extrait
    assert "Inscrit·e" in extrait
    assert "Pas de bulletin" in extrait
    assert "Aucune cotisation" in extrait


def test_la_pastille_1re_venue_reste_dans_la_meme_ligne(admin_client, app, seance):
    """Elle existait avant, sur sa propre ligne. Elle rejoint les autres :
    quatre informations sur quatre lignes, c'est une colonne qu'on ne lit
    plus."""
    with app.app_context():
        from app.extensions import db
        from app.models import STATUT_PARTICIPANT_ATTENTE, Participant

        p = db.session.get(Participant, seance["libre_id"])
        p.statut_inscription = STATUT_PARTICIPANT_ATTENTE
        db.session.commit()
        assert p.attend_premiere_participation

    page = _page(admin_client, seance)
    assert "1re venue" in page
    debut = page.index("1re venue")
    # La pastille est DANS le conteneur des autres, pas au-dessus.
    avant = page[max(0, debut - 400):debut]
    assert "situ-ligne" in avant
