"""La participation répartie entre les secteurs, au prorata des venues.

L'adhésion couvre l'assurance : c'est la part légale, elle ne se découpe
pas. La participation, elle, finance les secteurs — et jusqu'ici elle
allait en entier au « secteur qui fait venir ». Faux dès que la personne
circule : venir trente fois dans l'année en faisant vivre six secteurs, et
n'en créditer qu'un seul, ce n'est pas une approximation, c'est une
répartition budgétaire fausse.

Deux exigences dominent tout le fichier :

1. **Aucun euro ne disparaît.** La somme des parts de tous les secteurs
   doit faire exactement la somme des participations. Au centime. Un
   tableau dont les colonnes ne totalisent pas l'encaissé est un tableau
   que la comptabilité renvoie, et elle a raison.

2. **Le calcul est reproductible.** Deux exports du même jour doivent se
   ressembler, y compris quand des restes d'arrondi sont à égalité.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# L'arrondi, isolé — c'est la pièce qui doit être irréprochable
# ---------------------------------------------------------------------------

def test_dix_euros_en_trois_parts_egales_font_dix_euros():
    """L'arrondi naïf donnerait 3,33 × 3 = 9,99 € et un centime fantôme."""
    from app.services.prorata import decouper

    parts = decouper(10.0, {"A": 1, "B": 1, "C": 1})
    assert round(sum(parts.values()), 2) == 10.0
    assert sorted(parts.values()) == [3.33, 3.33, 3.34]


def test_le_cas_reel_boucle_au_centime():
    """Le cas décrit par l'accueil : 20/2/2/2/1/1 pour 20 € de participation.

    Soit 28 venues. Ce total ne se devine pas, il se compte — d'où la
    somme explicite ci-dessous plutôt qu'un nombre écrit à la main : une
    proportion calculée sur un dénominateur faux est une erreur qui ne se
    voit sur aucun écran.
    """
    from app.services.prorata import decouper

    poids = {
        "Numérique": 20, "EPE": 2, "Santé Transition": 2,
        "Insertion Sociale et Professionnelle": 2,
        "Animation Globale": 1, "Familles": 1,
    }
    total = sum(poids.values())
    parts = decouper(20.0, poids)
    assert round(sum(parts.values()), 2) == 20.0
    assert parts["Numérique"] == round(20.0 * 20 / total, 2)


@pytest.mark.parametrize("montant", [0.01, 0.99, 7.0, 12.5, 20.0, 33.33, 150.0, 1234.56])
@pytest.mark.parametrize("poids", [
    {"A": 1}, {"A": 1, "B": 1}, {"A": 1, "B": 1, "C": 1}, {"A": 7, "B": 3},
    {"A": 20, "B": 2, "C": 2, "D": 2, "E": 1, "F": 1}, {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5},
])
def test_la_somme_est_toujours_exacte(montant, poids):
    """Balayage : aucun couple (montant, répartition) ne doit perdre un centime."""
    from app.services.prorata import decouper

    parts = decouper(montant, poids)
    assert round(sum(parts.values()), 2) == round(montant, 2)


def test_lordre_des_cles_ne_change_pas_le_resultat():
    """Deux exports du même jour doivent se ressembler.

    Avec trois parts égales, un centime doit être arbitré : si l'arbitrage
    dépendait de l'ordre du dictionnaire, le même calcul rendrait deux
    résultats.
    """
    from app.services.prorata import decouper

    a = decouper(10.0, {"A": 1, "B": 1, "C": 1})
    b = decouper(10.0, {"C": 1, "B": 1, "A": 1})
    assert a == b


def test_un_montant_nul_ne_cree_pas_de_centimes():
    from app.services.prorata import decouper

    parts = decouper(0.0, {"A": 3, "B": 1})
    assert parts == {"A": 0.0, "B": 0.0}


def test_sans_poids_rien_a_repartir():
    from app.services.prorata import decouper

    assert decouper(20.0, {}) == {}
    assert decouper(20.0, {"A": 0}) == {}


# ---------------------------------------------------------------------------
# Le décor : une personne, six secteurs, trente venues
# ---------------------------------------------------------------------------

#: Le scénario décrit par l'accueil : 30 venues, dont 20 en Numérique.
SCENARIO = [
    ("Numérique", 20),
    ("EPE", 2),
    ("Santé Transition", 2),
    ("Insertion Sociale et Professionnelle", 2),
    ("Animation Globale", 1),
    ("Familles", 1),
]


@pytest.fixture()
def annee_type(app):
    """Une année scolaire 2025-2026 avec le scénario ci-dessus."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import (
            AtelierActivite, Cotisation, Paiement, Participant,
            PresenceActivite, SessionActivite,
        )

        personne = Participant(nom=f"Prorata{suf}", prenom="Nadia",
                               created_secteur="Numérique")
        db.session.add(personne)
        db.session.flush()

        ateliers, sessions = [], []
        for secteur, nombre in SCENARIO:
            atelier = AtelierActivite(secteur=secteur, nom=f"At{suf}{secteur[:4]}")
            db.session.add(atelier)
            db.session.flush()
            ateliers.append(atelier.id)
            for i in range(nombre):
                s = SessionActivite(
                    atelier_id=atelier.id, secteur=secteur, session_type="COLLECTIF",
                    date_session=date(2025, 10, 1) + __import__("datetime").timedelta(days=i),
                    heure_debut="14:00", heure_fin="16:00")
                db.session.add(s)
                db.session.flush()
                sessions.append(s.id)
                db.session.add(PresenceActivite(session_id=s.id, participant_id=personne.id))

        participation = Cotisation(
            annee_scolaire=2025, type_cotisation="participation",
            participant_id=personne.id, montant_du=20.0,
            date_reference=date(2025, 9, 15))
        db.session.add(participation)
        db.session.flush()
        db.session.add(Paiement(cotisation_id=participation.id, montant=20.0,
                                date_paiement=date(2025, 9, 15), mode="especes"))
        db.session.commit()

        contexte = {"participant_id": personne.id, "ateliers": ateliers,
                    "sessions": sessions, "cotisation_id": participation.id,
                    "nom": personne.nom}

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import (
            ArchiveEmargement, AtelierActivite, Participant, PresenceActivite,
        )

        # SQLite n'applique pas ON DELETE CASCADE et recycle les identifiants :
        # on démonte à la main, sinon les présences orphelines seront léguées
        # au prochain atelier créé.
        for sid in contexte["sessions"]:
            for pr in PresenceActivite.query.filter_by(session_id=sid).all():
                db.session.delete(pr)
        for aid in contexte["ateliers"]:
            for archive in ArchiveEmargement.query.filter_by(atelier_id=aid).all():
                db.session.delete(archive)
        db.session.flush()
        for aid in contexte["ateliers"]:
            a = db.session.get(AtelierActivite, aid)
            if a is not None:
                db.session.delete(a)
        p = db.session.get(Participant, contexte["participant_id"])
        if p is not None:
            db.session.delete(p)
        db.session.commit()


# ---------------------------------------------------------------------------
# Le comptage des venues
# ---------------------------------------------------------------------------

def test_les_venues_sont_comptees_par_secteur(app, annee_type):
    with app.app_context():
        from app.services.prorata import venues_par_secteur

        compte = venues_par_secteur(2025, [annee_type["participant_id"]])
        assert compte[annee_type["participant_id"]] == dict(SCENARIO)


def test_une_absence_excusee_nest_pas_une_venue(app, annee_type):
    """Elle atteste que la personne N'EST PAS venue : le secteur ne l'a pas
    accueillie, il ne touche pas la part."""
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite, SessionActivite
        from app.services.prorata import venues_par_secteur

        epe = (SessionActivite.query
               .filter(SessionActivite.id.in_(annee_type["sessions"]))
               .filter_by(secteur="EPE").all())
        for s in epe:
            pr = PresenceActivite.query.filter_by(
                session_id=s.id, participant_id=annee_type["participant_id"]).one()
            pr.presence_type = "absent_excuse"
        db.session.commit()

        compte = venues_par_secteur(2025, [annee_type["participant_id"]])
        assert "EPE" not in compte[annee_type["participant_id"]]


def test_un_retard_est_une_venue(app, annee_type):
    """La personne est venue, en retard. Le secteur l'a bien accueillie."""
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite, SessionActivite
        from app.services.prorata import venues_par_secteur

        s = (SessionActivite.query
             .filter(SessionActivite.id.in_(annee_type["sessions"]))
             .filter_by(secteur="Familles").first())
        pr = PresenceActivite.query.filter_by(
            session_id=s.id, participant_id=annee_type["participant_id"]).one()
        pr.presence_type = "retard"
        db.session.commit()

        compte = venues_par_secteur(2025, [annee_type["participant_id"]])
        assert compte[annee_type["participant_id"]]["Familles"] == 1


def test_une_seance_annulee_naccueille_personne(app, annee_type):
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.prorata import venues_par_secteur

        for s in (SessionActivite.query
                  .filter(SessionActivite.id.in_(annee_type["sessions"]))
                  .filter_by(secteur="EPE").all()):
            s.statut = "annulee"
        db.session.commit()

        compte = venues_par_secteur(2025, [annee_type["participant_id"]])
        assert "EPE" not in compte[annee_type["participant_id"]]


def test_une_seance_hors_annee_scolaire_ne_compte_pas(app, annee_type):
    """Septembre à août : une séance de septembre 2026 appartient à l'année
    suivante, pas à celle qu'on répartit."""
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.prorata import venues_par_secteur

        s = (SessionActivite.query
             .filter(SessionActivite.id.in_(annee_type["sessions"]))
             .filter_by(secteur="Animation Globale").first())
        s.date_session = date(2026, 9, 15)
        db.session.commit()

        compte = venues_par_secteur(2025, [annee_type["participant_id"]])
        assert "Animation Globale" not in compte[annee_type["participant_id"]]


# ---------------------------------------------------------------------------
# La répartition
# ---------------------------------------------------------------------------

def test_le_scenario_de_laccueil(app, annee_type):
    """Le cas décrit par l'accueil : 20 venues en Numérique sur 28."""
    with app.app_context():
        from app.services.prorata import repartition

        vue = repartition(2025)
        ligne = next(p for p in vue["personnes"]
                     if p["participant"].id == annee_type["participant_id"])

        total = sum(nombre for _, nombre in SCENARIO)
        assert ligne["total_venues"] == total
        assert ligne["repli"] is False
        assert ligne["parts"]["Numérique"]["venues"] == 20
        assert ligne["parts"]["Numérique"]["regle"] == round(20.0 * 20 / total, 2)
        assert ligne["parts"]["Familles"]["venues"] == 1
        # Et surtout : les six parts refont les 20 € exactement.
        assert round(sum(p["regle"] for p in ligne["parts"].values()), 2) == 20.0
        assert round(sum(p["du"] for p in ligne["parts"].values()), 2) == 20.0


def test_aucun_euro_ne_disparait(app, annee_type):
    """L'invariant qui tient tout : la somme des secteurs = la somme des
    participations. C'est ce que la comptabilité vérifiera en premier."""
    with app.app_context():
        from app.services.prorata import repartition

        vue = repartition(2025)
        somme_secteurs_du = round(sum(c["du"] for c in vue["secteurs"].values()), 2)
        somme_secteurs_regle = round(sum(c["regle"] for c in vue["secteurs"].values()), 2)

        assert somme_secteurs_du == vue["totaux"]["du"]
        assert somme_secteurs_regle == vue["totaux"]["regle"]


def test_les_totaux_sont_la_somme_du_detail(app, annee_type):
    """Jamais deux calculs séparés : ils finiraient par diverger et
    personne ne saurait lequel croire."""
    with app.app_context():
        from app.services.prorata import repartition

        vue = repartition(2025)
        attendu: dict[str, float] = {}
        for personne in vue["personnes"]:
            for nom, part in personne["parts"].items():
                attendu[nom] = round(attendu.get(nom, 0.0) + part["regle"], 2)

        for nom, case in vue["secteurs"].items():
            assert case["regle"] == attendu[nom], nom


def test_un_reglement_partiel_ne_repartit_que_lencaisse(app, annee_type):
    """Le dû est le budget théorique, l'encaissé ce qu'un référent peut
    réellement engager. Les deux doivent se répartir séparément."""
    with app.app_context():
        from app.extensions import db
        from app.models import Paiement
        from app.services.prorata import repartition

        for versement in Paiement.query.filter_by(
                cotisation_id=annee_type["cotisation_id"]).all():
            versement.montant = 10.0
        db.session.commit()

        vue = repartition(2025)
        ligne = next(p for p in vue["personnes"]
                     if p["participant"].id == annee_type["participant_id"])
        assert ligne["du"] == 20.0
        assert ligne["regle"] == 10.0
        assert round(sum(p["du"] for p in ligne["parts"].values()), 2) == 20.0
        assert round(sum(p["regle"] for p in ligne["parts"].values()), 2) == 10.0
        assert ligne["parts"]["Numérique"]["du"] > ligne["parts"]["Numérique"]["regle"]


def test_sans_aucune_venue_tout_va_au_secteur_orienteur(app, annee_type):
    """Elle a payé, l'argent doit atterrir quelque part. On retombe alors
    exactement sur l'ancienne règle."""
    with app.app_context():
        from app.extensions import db
        from app.models import PresenceActivite
        from app.services.prorata import repartition

        for pr in PresenceActivite.query.filter_by(
                participant_id=annee_type["participant_id"]).all():
            db.session.delete(pr)
        db.session.commit()

        vue = repartition(2025)
        ligne = next(p for p in vue["personnes"]
                     if p["participant"].id == annee_type["participant_id"])
        assert ligne["repli"] is True
        assert ligne["total_venues"] == 0
        assert list(ligne["parts"]) == ["Numérique"]
        assert ligne["parts"]["Numérique"]["regle"] == 20.0
        # Une part de repli ne s'invente pas des venues.
        assert ligne["parts"]["Numérique"]["venues"] == 0
        assert vue["totaux"]["nb_repli"] == 1


def test_une_adhesion_nest_jamais_repartie(app, annee_type):
    """L'adhésion couvre l'assurance : c'est la part légale, elle ne se
    découpe pas. Seule la participation se répartit."""
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, Paiement
        from app.services.prorata import repartition

        adhesion = Cotisation(
            annee_scolaire=2025, type_cotisation="adhesion_individuelle",
            participant_id=annee_type["participant_id"], montant_du=15.0,
            date_reference=date(2025, 9, 15))
        db.session.add(adhesion)
        db.session.flush()
        db.session.add(Paiement(cotisation_id=adhesion.id, montant=15.0,
                                date_paiement=date(2025, 9, 15), mode="especes"))
        db.session.commit()

        vue = repartition(2025)
        # Les 15 € d'adhésion n'entrent nulle part dans la répartition.
        assert vue["totaux"]["regle"] == 20.0


def test_un_secteur_mal_orthographie_ne_cree_pas_un_doublon(app, annee_type):
    """« Numerique » sans accent est le même secteur que « Numérique ».

    Le laisser passer créerait un septième secteur fantôme avec de l'argent
    dedans — le doublon qu'on a déjà combattu sur les villes.
    """
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.prorata import repartition

        seances = (SessionActivite.query
                   .filter(SessionActivite.id.in_(annee_type["sessions"]))
                   .filter_by(secteur="Numérique").limit(5).all())
        for s in seances:
            s.secteur = "NUMERIQUE"
        db.session.commit()

        vue = repartition(2025)
        ligne = next(p for p in vue["personnes"]
                     if p["participant"].id == annee_type["participant_id"])
        assert "NUMERIQUE" not in ligne["parts"]
        assert ligne["parts"]["Numérique"]["venues"] == 20


def test_un_secteur_inconnu_nest_jamais_jete(app, annee_type):
    """Il porte de l'argent. Mieux vaut le voir avec une orthographe
    bancale — c'est visible donc corrigeable — que le voir disparaître."""
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.prorata import repartition

        s = (SessionActivite.query
             .filter(SessionActivite.id.in_(annee_type["sessions"]))
             .filter_by(secteur="Familles").first())
        s.secteur = "Secteur qui n'existe pas"
        db.session.commit()

        vue = repartition(2025)
        assert "Secteur qui n'existe pas" in vue["secteurs"]
        # Et l'invariant tient toujours.
        assert round(sum(c["regle"] for c in vue["secteurs"].values()), 2) == \
            vue["totaux"]["regle"]


def test_une_annee_sans_participation_ne_plante_pas(app):
    with app.app_context():
        from app.services.prorata import repartition

        vue = repartition(1999)
        assert vue["personnes"] == []
        assert vue["secteurs"] == {}
        assert vue["totaux"]["du"] == 0.0


# ---------------------------------------------------------------------------
# À l'échelle : c'est l'accumulation qui fait apparaître les centimes perdus
# ---------------------------------------------------------------------------

def test_aucun_centime_ne_se_perd_sur_toute_une_annee(app):
    """Quarante personnes, des montants biscornus, des venues irrégulières.

    L'invariant sur une seule participation ne prouve rien : un arrondi
    boiteux se rattrape tout seul sur un cas et dérive sur cent. C'est ce
    test-là qui dit si le tableau de la comptabilité tombera juste.
    """
    import random

    suf = _suffixe()
    alea = random.Random(20260916)  # graine fixe : un échec doit être rejouable
    secteurs = [nom for nom, _ in SCENARIO]

    with app.app_context():
        from app.extensions import db
        from app.models import (
            AtelierActivite, Cotisation, Paiement, Participant,
            PresenceActivite, SessionActivite,
        )
        from app.services.prorata import repartition

        ateliers = {}
        for secteur in secteurs:
            atelier = AtelierActivite(secteur=secteur, nom=f"Masse{suf}{secteur[:4]}")
            db.session.add(atelier)
            db.session.flush()
            ateliers[secteur] = atelier

        # Un stock de séances réutilisables, pour ne pas en créer des milliers.
        seances = {}
        for secteur, atelier in ateliers.items():
            seances[secteur] = []
            for i in range(12):
                s = SessionActivite(
                    atelier_id=atelier.id, secteur=secteur, session_type="COLLECTIF",
                    date_session=date(2019, 10, 1) + __import__("datetime").timedelta(days=i),
                    heure_debut="14:00", heure_fin="16:00")
                db.session.add(s)
                db.session.flush()
                seances[secteur].append(s)

        gens = []
        attendu_du = attendu_regle = 0.0
        for n in range(40):
            personne = Participant(nom=f"Masse{suf}{n:02d}", prenom="Test",
                                   created_secteur=alea.choice(secteurs))
            db.session.add(personne)
            db.session.flush()
            gens.append(personne)

            # Des venues irrégulières — certains ne viennent nulle part.
            for secteur in alea.sample(secteurs, alea.randint(0, len(secteurs))):
                for s in alea.sample(seances[secteur], alea.randint(1, 5)):
                    db.session.add(
                        PresenceActivite(session_id=s.id, participant_id=personne.id))

            # Des montants qui ne tombent pas rond, et des règlements partiels.
            du = round(alea.choice([5.0, 12.5, 20.0, 33.33, 7.77, 41.09]), 2)
            cot = Cotisation(annee_scolaire=2019, type_cotisation="participation",
                             participant_id=personne.id, montant_du=du,
                             date_reference=date(2019, 9, 15))
            db.session.add(cot)
            db.session.flush()
            verse = round(du * alea.choice([0, 0.25, 0.5, 1.0]), 2)
            if verse:
                db.session.add(Paiement(cotisation_id=cot.id, montant=verse,
                                        date_paiement=date(2019, 9, 20), mode="especes"))
            attendu_du = round(attendu_du + du, 2)
            attendu_regle = round(attendu_regle + verse, 2)
        db.session.commit()

        try:
            vue = repartition(2019)

            # 1. Les totaux correspondent à ce qu'on a réellement saisi.
            assert vue["totaux"]["du"] == attendu_du
            assert vue["totaux"]["regle"] == attendu_regle

            # 2. LA vérification : la somme des colonnes fait le total.
            assert round(sum(c["du"] for c in vue["secteurs"].values()), 2) == attendu_du
            assert round(sum(c["regle"] for c in vue["secteurs"].values()), 2) == attendu_regle

            # 3. Et personne à personne, chaque découpe boucle aussi.
            for ligne in vue["personnes"]:
                assert round(sum(p["du"] for p in ligne["parts"].values()), 2) == ligne["du"]
                assert round(sum(p["regle"] for p in ligne["parts"].values()), 2) == ligne["regle"]
        finally:
            for personne in gens:
                for pr in PresenceActivite.query.filter_by(participant_id=personne.id).all():
                    db.session.delete(pr)
            db.session.flush()
            for personne in gens:
                db.session.delete(personne)
            db.session.flush()
            for atelier in ateliers.values():
                db.session.delete(atelier)
            db.session.commit()


# ---------------------------------------------------------------------------
# « Au 31 décembre » doit vouloir dire au 31 décembre
# ---------------------------------------------------------------------------

def test_une_date_darrete_ne_compte_que_ce_quon_savait_ce_jour_la(app, annee_type):
    """Un arrêté qui compterait les venues de février n'est pas un arrêté.

    On déplace toutes les séances EPE en février, après la date d'arrêté :
    elles doivent disparaître de la photo au 31 décembre, et les parts se
    redistribuer entre les secteurs restants — sans perdre un centime.
    """
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.prorata import repartition

        for i, s in enumerate(SessionActivite.query
                              .filter(SessionActivite.id.in_(annee_type["sessions"]))
                              .filter_by(secteur="EPE").all()):
            s.date_session = date(2026, 2, 10 + i)
        db.session.commit()

        au_31_12 = repartition(2025, a_la_date=date(2025, 12, 31))
        ligne = next(p for p in au_31_12["personnes"]
                     if p["participant"].id == annee_type["participant_id"])
        assert "EPE" not in ligne["parts"]
        assert round(sum(p["regle"] for p in ligne["parts"].values()), 2) == 20.0

        # Et l'année entière, elle, les voit bien.
        entiere = repartition(2025)
        ligne_entiere = next(p for p in entiere["personnes"]
                             if p["participant"].id == annee_type["participant_id"])
        assert ligne_entiere["parts"]["EPE"]["venues"] == 2


def test_un_versement_posterieur_a_larrete_nest_pas_encaisse(app, annee_type):
    """Un chèque de mars n'a pas à figurer dans l'arrêté de décembre."""
    with app.app_context():
        from app.extensions import db
        from app.models import Paiement
        from app.services.prorata import repartition

        for versement in Paiement.query.filter_by(
                cotisation_id=annee_type["cotisation_id"]).all():
            versement.date_paiement = date(2026, 3, 5)
        db.session.commit()

        au_31_12 = repartition(2025, a_la_date=date(2025, 12, 31))
        ligne = next(p for p in au_31_12["personnes"]
                     if p["participant"].id == annee_type["participant_id"])
        # Le dû est bien là (la participation existait), l'encaissé non.
        assert ligne["du"] == 20.0
        assert ligne["regle"] == 0.0
        assert au_31_12["totaux"]["regle"] == 0.0


def test_une_participation_creee_apres_larrete_nen_fait_pas_partie(app, annee_type):
    with app.app_context():
        from app.services.prorata import repartition

        # La participation a pour date de référence le 15/09/2025.
        avant = repartition(2025, a_la_date=date(2025, 9, 1))
        assert all(p["participant"].id != annee_type["participant_id"]
                   for p in avant["personnes"])


# ---------------------------------------------------------------------------
# L'arrêté : une photo qui ne bouge plus
# ---------------------------------------------------------------------------

@pytest.fixture()
def sans_arretes(app):
    """Table nettoyée avant et après : un arrêté d'un autre test fausserait
    les comptes, et la contrainte d'unicité ferait échouer la création."""
    def _vider():
        from app.extensions import db
        from app.models import RepartitionArretee

        for a in RepartitionArretee.query.all():
            db.session.delete(a)
        db.session.commit()

    with app.app_context():
        _vider()
    yield
    with app.app_context():
        _vider()


def test_arreter_ecrit_le_detail_et_les_totaux(app, annee_type, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter

        arrete, message = arreter(2025, date(2026, 8, 31), libelle="Clôture d'année")
        assert arrete is not None, message
        assert arrete.total_regle == 20.0
        assert arrete.total_du == 20.0
        # Une ligne par secteur fréquenté.
        secteurs_ecrits = {ligne.secteur for ligne in arrete.lignes}
        assert secteurs_ecrits == {nom for nom, _ in SCENARIO}
        # Et les totaux par secteur se relisent depuis le détail.
        totaux = arrete.totaux_par_secteur()
        assert round(sum(c["regle"] for c in totaux.values()), 2) == 20.0
        assert totaux["Numérique"]["venues"] == 20


def test_un_arrete_ne_bouge_plus(app, annee_type, sans_arretes):
    """C'est toute sa raison d'être : un référent à qui on annonce une somme
    ne doit pas la voir fondre parce que quelqu'un a fréquenté ailleurs."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, PresenceActivite, SessionActivite
        from app.services.prorata import arreter, repartition

        arrete, message = arreter(2025, date(2026, 8, 31))
        assert arrete is not None, message
        fige = arrete.totaux_par_secteur()["Numérique"]["regle"]

        # Cinquante venues de plus en Familles, après l'arrêté.
        atelier = db.session.get(AtelierActivite, annee_type["ateliers"][-1])
        for i in range(50):
            s = SessionActivite(
                atelier_id=atelier.id, secteur="Familles", session_type="COLLECTIF",
                date_session=date(2026, 5, 1), heure_debut="14:00", heure_fin="16:00")
            db.session.add(s)
            db.session.flush()
            db.session.add(PresenceActivite(
                session_id=s.id, participant_id=annee_type["participant_id"]))
        db.session.commit()

        # Le calcul vivant a bougé…
        vivant = repartition(2025)["secteurs"]["Numérique"]["regle"]
        assert vivant < fige

        # …mais l'arrêté, non.
        from app.models import RepartitionArretee
        relu = db.session.get(RepartitionArretee, arrete.id)
        assert relu.totaux_par_secteur()["Numérique"]["regle"] == fige


def test_on_narrete_pas_avant_la_rentree(app, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter

        arrete, message = arreter(2025, date(2025, 6, 30))
        assert arrete is None
        assert "précède la rentrée" in message


def test_on_narrete_pas_une_date_future(app, sans_arretes):
    """Un « arrêté au 31 août » signé en mars annonce cinq mois qui n'ont
    pas eu lieu."""
    with app.app_context():
        from datetime import timedelta

        from app.services.prorata import arreter

        demain = date.today() + timedelta(days=1)
        annee = demain.year if demain.month >= 9 else demain.year - 1
        arrete, message = arreter(annee, demain)
        assert arrete is None
        assert "future" in message


def test_deux_arretes_a_la_meme_date_sont_refuses(app, annee_type, sans_arretes):
    """Deux pièces portant la même date, c'est la garantie qu'un jour
    quelqu'un cite la mauvaise."""
    with app.app_context():
        from app.services.prorata import arreter

        premier, _ = arreter(2025, date(2026, 8, 31))
        assert premier is not None
        second, message = arreter(2025, date(2026, 8, 31))
        assert second is None
        assert "existe déjà" in message


def test_un_arrete_reste_lisible_quand_la_fiche_disparait(app, annee_type, sans_arretes):
    """Une fiche peut être renommée, fusionnée ou supprimée. Un arrêté qui
    deviendrait illisible pour autant ne serait pas une pièce justificative."""
    with app.app_context():
        from app.extensions import db
        from app.models import RepartitionArreteeLigne
        from app.services.prorata import arreter

        arrete, message = arreter(2025, date(2026, 8, 31))
        assert arrete is not None, message
        ligne = arrete.lignes[0]
        assert ligne.participant_nom
        assert annee_type["nom"] in ligne.nom_affiche

        # On coupe le lien, comme le ferait la suppression de la fiche.
        ligne_id = ligne.id
        ligne.participant_id = None
        db.session.commit()
        relue = db.session.get(RepartitionArreteeLigne, ligne_id)
        assert annee_type["nom"] in relue.nom_affiche


def test_larrete_photographie_bien_la_date_demandee(app, annee_type, sans_arretes):
    """Un arrêté au 31 décembre ne doit pas compter les séances de février."""
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite
        from app.services.prorata import arreter

        for i, s in enumerate(SessionActivite.query
                              .filter(SessionActivite.id.in_(annee_type["sessions"]))
                              .filter_by(secteur="EPE").all()):
            s.date_session = date(2026, 2, 10 + i)
        db.session.commit()

        arrete, message = arreter(2025, date(2025, 12, 31))
        assert arrete is not None, message
        assert "EPE" not in arrete.totaux_par_secteur()
        assert round(arrete.total_regle, 2) == 20.0


def test_le_dernier_arrete_est_le_plus_recent(app, annee_type, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter, dernier_arrete

        arreter(2025, date(2025, 12, 31))
        arreter(2025, date(2026, 3, 31))
        assert dernier_arrete(2025).date_arrete == date(2026, 3, 31)


# ---------------------------------------------------------------------------
# L'écran : quatre métiers doivent y lire la même chose
# ---------------------------------------------------------------------------

def _page(admin_client, **params):
    from urllib.parse import urlencode

    r = admin_client.get("/repartition-participation?" + urlencode(params))
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def test_lecran_montre_la_repartition_par_secteur(admin_client, annee_type, sans_arretes):
    page = _page(admin_client, annee=2025)
    for secteur, _ in SCENARIO:
        assert secteur in page
    assert "Encaissé à répartir" in page
    assert "20.00 €" in page


def test_lecran_dit_que_le_chiffre_vivant_est_provisoire(admin_client, annee_type, sans_arretes):
    """LA mention qui évite d'engager un budget sur un chiffre qui bouge."""
    page = _page(admin_client, annee=2025)
    assert "Répartition provisoire" in page
    assert "bougera à chaque séance pointée" in page
    assert "arrêté figé" in page


def test_lecran_dun_arrete_dit_quil_ne_bouge_plus(admin_client, app, annee_type, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter

        arrete, message = arreter(2025, date(2026, 8, 31), libelle="Clôture d'année")
        assert arrete is not None, message
        arrete_id = arrete.id

    page = _page(admin_client, annee=2025, arrete=arrete_id)
    assert "Arrêté figé au 31/08/2026" in page
    assert "ne bougeront plus" in page
    # Et surtout : pas le bandeau du provisoire en même temps.
    assert "Répartition provisoire" not in page


def test_lecran_montre_le_detail_par_personne(admin_client, annee_type, sans_arretes):
    """« Pourquoi le Numérique a-t-il ce montant ? » doit se répondre ici,
    sans ouvrir trente fiches."""
    page = _page(admin_client, annee=2025)
    assert annee_type["nom"] in page
    assert "Détail par personne" in page
    # La part d'un secteur est affichée avec les venues qui la justifient.
    assert "Numérique × 20" in page


def test_le_filtre_secteur_ne_montre_que_ce_secteur(admin_client, annee_type, sans_arretes):
    page = _page(admin_client, annee=2025, secteur="EPE")
    assert "EPE" in page
    # Le tableau par secteur ne doit plus lister les autres.
    debut = page.index("Par secteur")
    tableau = page[debut:page.index("Détail par personne")]
    assert "Insertion Sociale et Professionnelle" not in tableau


def test_arreter_depuis_lecran(admin_client, app, annee_type, sans_arretes):
    r = admin_client.post("/repartition-participation/arreter", data={
        "annee": "2025", "date_arrete": "2026-08-31", "libelle": "Clôture",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "Répartition arrêtée au 31/08/2026" in r.get_data(as_text=True)

    with app.app_context():
        from app.models import RepartitionArretee

        assert RepartitionArretee.query.filter_by(annee_scolaire=2025).count() == 1


def test_une_date_darrete_invalide_ne_casse_pas_lecran(admin_client, sans_arretes):
    r = admin_client.post("/repartition-participation/arreter", data={
        "annee": "2025", "date_arrete": "pas une date",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "invalide" in r.get_data(as_text=True)


def test_supprimer_un_arrete_permet_de_le_refaire(admin_client, app, annee_type, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter

        arrete, _ = arreter(2025, date(2026, 8, 31))
        arrete_id = arrete.id

    r = admin_client.post(
        f"/repartition-participation/arrete/{arrete_id}/supprimer", follow_redirects=True)
    assert r.status_code == 200
    assert "Arrêté supprimé" in r.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db
        from app.models import RepartitionArretee, RepartitionArreteeLigne
        from app.services.prorata import arreter

        assert db.session.get(RepartitionArretee, arrete_id) is None
        # Les lignes partent avec : une cascade ORM, parce que SQLite
        # n'applique pas ON DELETE CASCADE tout seul.
        assert RepartitionArreteeLigne.query.filter_by(arrete_id=arrete_id).count() == 0
        refait, message = arreter(2025, date(2026, 8, 31))
        assert refait is not None, message


def test_lecran_est_ferme_sans_le_droit_cotisations(client):
    """Un visiteur non connecté ne voit pas la comptabilité des secteurs."""
    r = client.get("/repartition-participation")
    assert r.status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# La fiche participant : ce que l'accueil a sous les yeux
# ---------------------------------------------------------------------------

@pytest.fixture()
def cette_annee(app):
    """Un décor dans l'année scolaire EN COURS.

    La fiche participant n'affiche que la cotisation de l'année courante —
    c'est son travail. Un décor daté d'une année révolue n'y apparaîtrait
    pas, et le test passerait à côté de ce qu'il croit vérifier.
    """
    from app.services.cotisations import annee_scolaire_courante

    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import (
            AtelierActivite, Cotisation, Paiement, Participant,
            PresenceActivite, SessionActivite,
        )

        annee = annee_scolaire_courante()
        jour = date(annee, 9, 5)
        personne = Participant(nom=f"Fiche{suf}", prenom="Sam",
                               created_secteur="Numérique")
        db.session.add(personne)
        db.session.flush()

        ateliers = []
        for secteur, nombre in (("Numérique", 3), ("Familles", 1)):
            atelier = AtelierActivite(secteur=secteur, nom=f"Fi{suf}{secteur[:4]}")
            db.session.add(atelier)
            db.session.flush()
            ateliers.append(atelier.id)
            for i in range(nombre):
                s = SessionActivite(atelier_id=atelier.id, secteur=secteur,
                                    session_type="COLLECTIF", date_session=jour,
                                    heure_debut="14:00", heure_fin="16:00")
                db.session.add(s)
                db.session.flush()
                db.session.add(PresenceActivite(session_id=s.id,
                                                participant_id=personne.id))

        cot = Cotisation(annee_scolaire=annee, type_cotisation="participation",
                         participant_id=personne.id, montant_du=20.0,
                         date_reference=jour)
        db.session.add(cot)
        db.session.flush()
        db.session.add(Paiement(cotisation_id=cot.id, montant=20.0,
                                date_paiement=jour, mode="especes"))
        db.session.commit()
        contexte = {"participant_id": personne.id, "ateliers": ateliers, "annee": annee}

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import (
            ArchiveEmargement, AtelierActivite, Participant, PresenceActivite,
        )

        for pr in PresenceActivite.query.filter_by(
                participant_id=contexte["participant_id"]).all():
            db.session.delete(pr)
        for aid in contexte["ateliers"]:
            for archive in ArchiveEmargement.query.filter_by(atelier_id=aid).all():
                db.session.delete(archive)
        db.session.flush()
        for aid in contexte["ateliers"]:
            a = db.session.get(AtelierActivite, aid)
            if a is not None:
                db.session.delete(a)
        p = db.session.get(Participant, contexte["participant_id"])
        if p is not None:
            db.session.delete(p)
        db.session.commit()


def test_la_fiche_dit_ou_part_la_participation(admin_client, cette_annee):
    """« Et mes 20 €, ils vont où ? » doit se répondre sur la fiche, sans
    aller chercher un tableau de bord."""
    r = admin_client.get(f"/participants/{cette_annee['participant_id']}/synthese")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Réparti entre les secteurs" in page
    # Trois venues en Numérique sur quatre : 15,00 € des 20 €.
    assert "15.00 €" in page
    assert "5.00 €" in page


def test_lencart_de_la_fiche_dit_la_meme_chose_que_le_tableau(app, annee_type):
    """Deux calculs de la même chose finissent par diverger, et c'est
    l'accueil qui se fait contredire par le tableau de la direction."""
    with app.app_context():
        from app.extensions import db
        from app.models import Participant
        from app.services.prorata import repartition, repartition_personne

        fiche = db.session.get(Participant, annee_type["participant_id"])
        encart = repartition_personne(fiche, 2025)
        tableau = next(p for p in repartition(2025)["personnes"]
                       if p["participant"].id == fiche.id)
        assert encart["parts"] == tableau["parts"]


def test_sans_participation_pas_dencart(app, annee_type):
    """Afficher un bloc vide ferait croire à un bug."""
    with app.app_context():
        from app.extensions import db
        from app.models import Cotisation, Participant
        from app.services.prorata import repartition_personne

        cot = db.session.get(Cotisation, annee_type["cotisation_id"])
        db.session.delete(cot)
        db.session.commit()

        fiche = db.session.get(Participant, annee_type["participant_id"])
        assert repartition_personne(fiche, 2025) is None


# ---------------------------------------------------------------------------
# L'export : un classeur qui se défend tout seul
# ---------------------------------------------------------------------------

def _classeur(admin_client, **params):
    from io import BytesIO
    from urllib.parse import urlencode

    from openpyxl import load_workbook

    r = admin_client.get("/repartition-participation.xlsx?" + urlencode(params))
    assert r.status_code == 200, r.status_code
    assert "spreadsheetml" in r.headers["Content-Type"]
    return load_workbook(BytesIO(r.data))


def test_lexport_a_les_trois_onglets(admin_client, annee_type, sans_arretes):
    wb = _classeur(admin_client, annee=2025)
    assert wb.sheetnames == ["Par secteur", "Détail par personne", "Contrôle"]


def test_lexport_boucle_et_le_dit(admin_client, annee_type, sans_arretes):
    """L'onglet Contrôle fait le rapprochement que la comptabilité ferait à
    la main. L'écart doit être nul, et visible."""
    wb = _classeur(admin_client, annee=2025)
    controle = wb["Contrôle"]
    lignes = {controle.cell(row=r, column=1).value: (
        controle.cell(row=r, column=2).value, controle.cell(row=r, column=3).value)
        for r in range(5, 8)}
    assert lignes["Somme des parts par secteur"] == (20.0, 20.0)
    assert lignes["Somme des participations"] == (20.0, 20.0)
    assert lignes["Écart"] == (0.0, 0.0)


def test_lexport_dit_sil_est_provisoire(admin_client, annee_type, sans_arretes):
    """Un tableur circule par courriel, détaché de l'écran qui l'a produit.
    Trois mois plus tard, personne ne saura dire ce qu'il portait."""
    wb = _classeur(admin_client, annee=2025)
    for onglet in wb.sheetnames:
        nature = wb[onglet]["A2"].value or ""
        assert "PROVISOIRE" in nature, onglet


def test_lexport_dun_arrete_dit_quil_est_fige(admin_client, app, annee_type, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter

        arrete, message = arreter(2025, date(2026, 8, 31), libelle="Clôture d'année")
        assert arrete is not None, message
        arrete_id = arrete.id

    wb = _classeur(admin_client, annee=2025, arrete=arrete_id)
    for onglet in wb.sheetnames:
        nature = wb[onglet]["A2"].value or ""
        assert "ARRÊTÉ FIGÉ au 31/08/2026" in nature, onglet
        assert "PROVISOIRE" not in nature, onglet
    assert "Clôture d'année" in wb["Par secteur"]["A2"].value


def test_lexport_detaille_chaque_part(admin_client, annee_type, sans_arretes):
    wb = _classeur(admin_client, annee=2025)
    detail = wb["Détail par personne"]
    lignes = [
        [detail.cell(row=r, column=c).value for c in range(1, 8)]
        for r in range(5, detail.max_row + 1)
    ]
    par_secteur = {ligne[1]: ligne for ligne in lignes if ligne[0]}
    assert set(par_secteur) == {nom for nom, _ in SCENARIO}
    assert par_secteur["Numérique"][2] == 20          # venues dans ce secteur
    assert par_secteur["Numérique"][3] == 28          # total des venues
    # Et la somme des parts refait la participation.
    assert round(sum(ligne[5] for ligne in lignes if ligne[0]), 2) == 20.0


def test_le_nom_du_fichier_dit_ce_quil_contient(admin_client, annee_type, sans_arretes):
    r = admin_client.get("/repartition-participation.xlsx?annee=2025")
    entete = r.headers["Content-Disposition"]
    assert "repartition_participation" in entete
    assert "2025-2026" in entete
    assert "provisoire" in entete


# ---------------------------------------------------------------------------
# La période couverte : « 2025-2026 » ne veut rien dire pour une comptabilité
# ---------------------------------------------------------------------------

def test_la_periode_dune_annee_revolue_va_de_septembre_a_aout(app, annee_type):
    with app.app_context():
        from app.services.prorata import repartition

        vue = repartition(2025)
        assert vue["periode"]["debut"] == date(2025, 9, 1)
        assert vue["periode"]["fin"] == date(2026, 8, 31)


def test_la_periode_de_lannee_en_cours_sarrete_aujourdhui(app):
    """Annoncer une période qui va jusqu'en août laisserait croire que les
    mois à venir sont déjà comptés."""
    with app.app_context():
        from app.services.cotisations import annee_scolaire_courante
        from app.services.prorata import repartition

        annee = annee_scolaire_courante()
        vue = repartition(annee)
        assert vue["periode"]["debut"] == date(annee, 9, 1)
        assert vue["periode"]["fin"] == date.today()


def test_la_periode_dun_arrete_sarrete_a_sa_date(app, annee_type, sans_arretes):
    with app.app_context():
        from app.services.prorata import arreter, periode_couverte

        arrete, message = arreter(2025, date(2025, 12, 31))
        assert arrete is not None, message
        debut, fin = periode_couverte(arrete.annee_scolaire, arrete.date_arrete)
        assert (debut, fin) == (date(2025, 9, 1), date(2025, 12, 31))


def test_lecran_annonce_ses_bornes_en_dates(admin_client, annee_type, sans_arretes):
    page = _page(admin_client, annee=2025)
    assert "Période couverte : du 01/09/2025" in page
    assert "31/08/2026" in page
    # Et prévient de la confusion avec l'exercice comptable.
    assert "exercice comptable" in page


def test_lexport_porte_ses_bornes_sur_chaque_onglet(admin_client, annee_type, sans_arretes):
    """Le classeur circulera loin de l'écran qui l'a produit : la période
    doit y être écrite, pas déductible."""
    wb = _classeur(admin_client, annee=2025)
    for onglet in wb.sheetnames:
        couverture = wb[onglet]["A3"].value or ""
        assert "Période couverte : du 01/09/2025 au 31/08/2026" in couverture, onglet
        assert "exercice comptable" in couverture, onglet
