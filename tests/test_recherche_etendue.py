"""La recherche globale connaît enfin les séances, les dépenses et les locations.

Elle couvrait six types : participant, quartier, projet, subvention, atelier,
partenaire. Il manquait la séance — l'objet le plus manipulé de
l'application : on cherchait l'atelier, puis on déroulait sa liste pour
retrouver la bonne date. Manquaient aussi la dépense (« la facture EDF de
mars ») et la réservation de salle (au téléphone, on a une référence de
contrat ou un nom d'association, jamais la date exacte).
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


def _chercher(client, terme, type_attendu=None):
    r = client.get(f"/api/global-search?q={terme}")
    assert r.status_code == 200
    resultats = r.get_json().get("results", [])
    if type_attendu:
        resultats = [x for x in resultats if x.get("type") == type_attendu]
    return resultats


# ---------------------------------------------------------------------------
# Séances
# ---------------------------------------------------------------------------

@pytest.fixture()
def seance(app):
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite

        a = AtelierActivite(secteur="Adultes", nom=f"Couture{suf}")
        db.session.add(a)
        db.session.flush()
        s = SessionActivite(
            atelier_id=a.id, secteur="Adultes", session_type="COLLECTIF",
            date_session=date(2026, 3, 12), heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(s)
        db.session.commit()
        contexte = {"id": s.id, "atelier": a.nom, "suffixe": suf}

    yield contexte

    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite

        for atelier in AtelierActivite.query.filter_by(nom=contexte["atelier"]).all():
            db.session.delete(atelier)
        db.session.commit()


def test_une_seance_se_trouve_par_son_atelier(admin_client, seance):
    trouves = _chercher(admin_client, seance["atelier"], "Séance")
    assert trouves, "la séance devrait remonter"
    assert seance["atelier"] in trouves[0]["label"]
    assert "12/03/2026" in trouves[0]["label"]
    assert f"session_id={seance['id']}" in trouves[0]["url"] or f"/{seance['id']}" in trouves[0]["url"]


def test_une_seance_se_trouve_par_sa_date(admin_client, seance):
    """« Couture 12/03 » : c'est comme ça qu'on cherche une séance."""
    trouves = _chercher(admin_client, f"{seance['atelier']} 12/03/2026", "Séance")
    assert len(trouves) == 1
    assert trouves[0]["label"].startswith(seance["atelier"])


def test_une_seance_annulee_le_dit(admin_client, app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        db.session.get(SessionActivite, seance["id"]).statut = "annulee"
        db.session.commit()

    trouves = _chercher(admin_client, seance["atelier"], "Séance")
    assert "annulée" in trouves[0]["meta"]


def test_une_seance_en_corbeille_ne_remonte_pas(admin_client, app, seance):
    with app.app_context():
        from app.extensions import db
        from app.models import SessionActivite

        db.session.get(SessionActivite, seance["id"]).is_deleted = True
        db.session.commit()

    assert _chercher(admin_client, seance["atelier"], "Séance") == []


def test_filtre_type_seance(admin_client, seance):
    """« type:séance couture » ne doit renvoyer que des séances."""
    r = admin_client.get(f"/api/global-search?q=type:séance {seance['atelier']}")
    resultats = r.get_json().get("results", [])
    assert resultats
    assert {x["type"] for x in resultats} == {"Séance"}


# ---------------------------------------------------------------------------
# Dépenses
# ---------------------------------------------------------------------------

@pytest.fixture()
def facture(app):
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Depense, LigneBudget, Subvention

        sub = Subvention(nom=f"CAF{suf}", secteur="Adultes", annee_exercice=2026)
        db.session.add(sub)
        db.session.flush()
        ligne = LigneBudget(subvention_id=sub.id, nature="charge", compte="606",
                            libelle="Énergie", montant_reel=5000.0)
        db.session.add(ligne)
        db.session.flush()
        d = Depense(ligne_budget_id=ligne.id, libelle=f"Électricité{suf}",
                    montant=432.10, fournisseur=f"Énergie{suf}",
                    reference_piece=f"REF{suf}", date_paiement=date(2026, 3, 5))
        db.session.add(d)
        db.session.commit()
        return {"id": d.id, "libelle": d.libelle, "fournisseur": d.fournisseur,
                "reference": d.reference_piece, "sub": sub.nom}


def test_une_depense_se_trouve_par_son_libelle(admin_client, facture):
    trouves = _chercher(admin_client, facture["libelle"], "Dépense")
    assert len(trouves) == 1
    assert "432.10 €" in trouves[0]["meta"]
    assert "05/03/2026" in trouves[0]["meta"]
    assert facture["sub"] in trouves[0]["meta"]


def test_une_depense_se_trouve_par_son_fournisseur(admin_client, facture):
    trouves = _chercher(admin_client, facture["fournisseur"], "Dépense")
    assert trouves and trouves[0]["label"] == facture["libelle"]


def test_une_depense_se_trouve_par_sa_reference(admin_client, facture):
    """Le numéro de pièce est ce qu'on a sous les yeux quand le comptable
    appelle."""
    trouves = _chercher(admin_client, facture["reference"], "Dépense")
    assert trouves and trouves[0]["label"] == facture["libelle"]


def test_une_depense_supprimee_ne_remonte_pas(admin_client, app, facture):
    with app.app_context():
        from app.extensions import db
        from app.models import Depense

        db.session.get(Depense, facture["id"]).est_supprimee = True
        db.session.commit()

    assert _chercher(admin_client, facture["libelle"], "Dépense") == []


# ---------------------------------------------------------------------------
# Réservations
# ---------------------------------------------------------------------------

@pytest.fixture()
def reservation(app):
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Espace, Preneur, Reservation, Site

        site = Site(nom=f"Centre{suf}", code=f"C{suf}")
        db.session.add(site)
        db.session.flush()
        salle = Espace(site_id=site.id, nom=f"Grande salle {suf}", reservable=True, louable=True)
        db.session.add(salle)
        preneur = Preneur(nom=f"Amicale{suf}", contact_nom="Mme Durand",
                          telephone="0344112233")
        db.session.add(preneur)
        db.session.flush()
        r = Reservation(reference=f"RES-{suf}", preneur_id=preneur.id,
                        espace_id=salle.id, titre=f"Loto{suf}", statut="confirmee")
        db.session.add(r)
        db.session.commit()
        return {"id": r.id, "reference": r.reference, "titre": r.titre,
                "preneur": preneur.nom, "salle": salle.nom}


def test_une_reservation_se_trouve_par_sa_reference(admin_client, reservation):
    trouves = _chercher(admin_client, reservation["reference"], "Réservation")
    assert len(trouves) == 1
    assert reservation["preneur"] in trouves[0]["label"]
    assert f"/{reservation['id']}" in trouves[0]["url"]


def test_une_reservation_se_trouve_par_le_nom_du_preneur(admin_client, reservation):
    """Au téléphone, on a le nom de l'association, pas la référence."""
    trouves = _chercher(admin_client, reservation["preneur"], "Réservation")
    assert trouves and reservation["reference"] in trouves[0]["meta"]


def test_une_reservation_se_trouve_par_son_titre(admin_client, reservation):
    trouves = _chercher(admin_client, reservation["titre"], "Réservation")
    assert trouves


# ---------------------------------------------------------------------------
# Cadre général
# ---------------------------------------------------------------------------

def test_les_neuf_types_sont_annonces():
    from app.main.recherche import SEARCH_TYPE_ALIASES, SEARCH_TYPE_PRIORITY

    assert set(SEARCH_TYPE_PRIORITY) == {
        "Participant", "Séance", "Quartier", "Projet", "Subvention",
        "Atelier", "Partenaire", "Dépense", "Réservation",
    }
    # Les mots qu'on tape vraiment mènent au bon type.
    for mot, cible in (("seance", "Séance"), ("facture", "Dépense"),
                       ("location", "Réservation"), ("salle", "Réservation")):
        assert SEARCH_TYPE_ALIASES[mot] == cible


def test_la_barre_annonce_ce_qu_elle_sait_chercher(admin_client):
    r = admin_client.get("/dashboard")
    assert "personne, séance, atelier, dépense" in r.get_data(as_text=True)


def test_la_page_de_resultats_repond(admin_client, seance):
    r = admin_client.get(f"/recherche?q={seance['atelier']}")
    assert r.status_code == 200
    assert seance["atelier"] in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Accents
# ---------------------------------------------------------------------------

def test_un_prenom_accentue_se_trouve_lui_meme(admin_client, app):
    """Régression : « Étienne » ne se trouvait pas, même tapé exactement
    comme il est enregistré.

    La recherche comparait ``lower(colonne) LIKE lower(motif)`` — mais le
    ``lower`` de Python connaît l'unicode (« É » -> « é ») et celui de
    SQLite ne descend que l'ASCII. Les deux côtés ne se rencontraient
    jamais. Tout prénom commençant par une majuscule accentuée était donc
    invisible : Étienne, Émilie, Éric, Élodie, Ünal…
    """
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        db.session.add(Participant(nom=f"Étienne{suf}", prenom="Amélie"))
        db.session.commit()

    for terme in (f"Étienne{suf}", f"étienne{suf}", f"ETIENNE{suf}", f"Etienne{suf}"):
        trouves = _chercher(admin_client, terme, "Participant")
        assert trouves, f"« {terme} » devrait retrouver la fiche"


def test_chercher_sans_accent_trouve_avec_accent(admin_client, app):
    """Personne ne tape les accents dans une barre de recherche."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Participant

        db.session.add(Participant(nom=f"Küçük{suf}", prenom="Amélie"))
        db.session.commit()

    assert _chercher(admin_client, f"kucuk{suf}", "Participant")
    assert _chercher(admin_client, f"Kucuk{suf}", "Participant")


def test_normalisation_identique_des_deux_cotes():
    """C'est la rupture de cette symétrie qui causait le bug."""
    from app.main.recherche import _normalize_search_text
    from app.services.recherche_texte import sans_accent

    for mot in ("Étienne", "AMÉLIE", "Küçük", "Nogent-sur-Oise", "  Zaïd  "):
        assert _normalize_search_text(mot) == sans_accent(mot.strip())

    assert sans_accent("Étienne") == "etienne"
    assert sans_accent("AMÉLIE") == "amelie"
    assert sans_accent(None) is None
