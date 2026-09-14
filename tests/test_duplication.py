"""Refaire ce qui existe déjà sans le ressaisir.

Deux gestes saisonniers coûtaient une saisie complète :
- **la rentrée** : un atelier reconduit à l'identique était recréé de zéro ;
- **la charge récurrente** : un loyer, un abonnement, une assurance, retapés
  chaque mois avec leur fournisseur et leur répartition entre financeurs.

On vérifie ici ce qui est copié, ce qui ne l'est surtout pas, et le cas
tordu des fins de mois.
"""
import uuid
from datetime import date

import pytest


def _suffixe():
    return uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Ateliers
# ---------------------------------------------------------------------------

@pytest.fixture()
def atelier_source(app):
    """Un atelier paramétré aux petits oignons, avec une séance déjà tenue."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, Competence, Referentiel, SessionActivite

        ref = Referentiel(nom=f"Référentiel {suf}")
        db.session.add(ref)
        db.session.flush()
        comp = Competence(referentiel_id=ref.id, code=f"C{suf}", nom=f"Compétence {suf}")
        db.session.add(comp)
        db.session.flush()

        a = AtelierActivite(
            secteur="Adultes",
            nom=f"Atelier informatique {suf}",
            description="Prise en main du numérique",
            type_atelier="COLLECTIF",
            capacite_defaut=12,
            duree_defaut_minutes=120,
            motifs_json='["Absence excusée"]',
            is_active=False,  # désactivé en fin d'année scolaire
        )
        a.competences = [comp]
        db.session.add(a)
        db.session.flush()

        db.session.add(SessionActivite(
            atelier_id=a.id, secteur="Adultes", date_session=date(2025, 6, 10),
        ))
        db.session.commit()
        contexte = {"id": a.id, "nom": a.nom, "competence_id": comp.id}

    yield contexte

    # Nettoyage : la page d'étiquetage Transitions ne liste que 200 ateliers.
    # Des fixtures qui s'accumulent finiraient par en pousser un hors liste
    # et feraient rougir un test d'un autre module, très loin d'ici.
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite

        for atelier in AtelierActivite.query.filter(
            AtelierActivite.nom.like(f"{contexte['nom']}%")
        ).all():
            db.session.delete(atelier)
        db.session.commit()


def test_duplication_atelier_reprend_le_parametrage(app, atelier_source):
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite
        from app.services.duplication import dupliquer_atelier

        source = db.session.get(AtelierActivite, atelier_source["id"])
        copie = dupliquer_atelier(source)
        db.session.commit()

        assert copie.id != source.id
        assert copie.secteur == "Adultes"
        assert copie.type_atelier == "COLLECTIF"
        assert copie.capacite_defaut == 12
        assert copie.duree_defaut_minutes == 120
        assert copie.description == "Prise en main du numérique"
        assert copie.motifs_json == '["Absence excusée"]'
        assert [c.id for c in copie.competences] == [atelier_source["competence_id"]]
        assert copie.nom == f"{atelier_source['nom']} (copie)"


def test_duplication_atelier_laisse_l_historique_a_l_original(app, atelier_source):
    """Une copie qui emporterait les séances de l'an dernier fausserait les
    bilans des deux ateliers d'un coup."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite, SessionActivite
        from app.services.duplication import dupliquer_atelier

        source = db.session.get(AtelierActivite, atelier_source["id"])
        copie = dupliquer_atelier(source)
        db.session.commit()

        assert SessionActivite.query.filter_by(atelier_id=copie.id).count() == 0
        assert SessionActivite.query.filter_by(atelier_id=source.id).count() == 1


def test_copie_naît_active_meme_si_l_original_dort(app, atelier_source):
    """On duplique précisément pour rouvrir l'atelier à la rentrée."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite
        from app.services.duplication import dupliquer_atelier

        source = db.session.get(AtelierActivite, atelier_source["id"])
        assert source.is_active is False
        copie = dupliquer_atelier(source)
        db.session.commit()
        assert copie.is_active is True
        assert copie.is_deleted is False


def test_pas_de_continuite_statistique_automatique(app, atelier_source):
    """Le lien de continuité additionne les chiffres de deux ateliers. On ne
    le pose jamais dans le dos : dupliquer sert aussi à créer une variante."""
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite
        from app.services.duplication import dupliquer_atelier

        source = db.session.get(AtelierActivite, atelier_source["id"])
        copie = dupliquer_atelier(source)
        db.session.commit()
        assert copie.continuity_parent_id is None


def test_nom_tres_long_ne_deborde_pas_de_la_colonne(app):
    with app.app_context():
        from app.extensions import db
        from app.models import AtelierActivite
        from app.services.duplication import dupliquer_atelier

        a = AtelierActivite(secteur="Adultes", nom="X" * 199)
        db.session.add(a)
        db.session.commit()
        copie = dupliquer_atelier(a)
        db.session.commit()
        assert len(copie.nom) <= 200
        assert copie.nom.endswith("(copie)")

        db.session.delete(copie)
        db.session.delete(a)
        db.session.commit()


def test_route_duplication_atelier(admin_client, app, atelier_source):
    """Un clic, et on atterrit sur l'édition de la copie pour la renommer."""
    r = admin_client.post(f"/activite/atelier/{atelier_source['id']}/dupliquer")
    assert r.status_code == 302
    assert "/edit" in r.headers["Location"]

    with app.app_context():
        from app.models import AtelierActivite

        copie = AtelierActivite.query.filter_by(nom=f"{atelier_source['nom']} (copie)").one()
        assert copie.capacite_defaut == 12


def test_bouton_dupliquer_present_dans_la_liste(admin_client, atelier_source):
    # Le fixture est désactivé (fin d'année) : la liste les masque par défaut.
    r = admin_client.get("/activite/?inactifs=1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f"/activite/atelier/{atelier_source['id']}/dupliquer" in html
    assert "Dupliquer" in html


# ---------------------------------------------------------------------------
# Dépenses
# ---------------------------------------------------------------------------

@pytest.fixture()
def depense_source(app):
    """Un loyer de janvier, réparti entre deux financeurs."""
    suf = _suffixe()
    with app.app_context():
        from app.extensions import db
        from app.models import Depense, DepenseAffectation, LigneBudget, Subvention

        sub = Subvention(nom=f"CAF {suf}", secteur="Adultes", annee_exercice=2026)
        db.session.add(sub)
        db.session.flush()
        ligne = LigneBudget(subvention_id=sub.id, nature="charge", compte="613", libelle="Loyers")
        db.session.add(ligne)
        db.session.flush()

        dep = Depense(
            ligne_budget_id=ligne.id,
            libelle="Loyer du local",
            montant=800.0,
            fournisseur="Mairie de Creil",
            mode_paiement="Virement",
            type_depense="Fonctionnement",
            reference_piece="FAC-2026-001",
            date_paiement=date(2026, 1, 31),
        )
        db.session.add(dep)
        db.session.flush()
        db.session.add(DepenseAffectation(
            depense_id=dep.id, source_type="subvention", subvention_id=sub.id,
            ligne_budget_id=ligne.id, montant=500.0,
        ))
        db.session.add(DepenseAffectation(
            depense_id=dep.id, source_type="fonds_propres",
            libelle_source="Fonds propres", montant=300.0,
        ))
        db.session.commit()
        return {"id": dep.id, "subvention_id": sub.id, "ligne_id": ligne.id}


def test_duplication_depense_reprend_tout_sauf_la_piece(app, depense_source):
    with app.app_context():
        from app.extensions import db
        from app.models import Depense
        from app.services.duplication import dupliquer_depense

        source = db.session.get(Depense, depense_source["id"])
        copie = dupliquer_depense(source)
        db.session.commit()

        assert copie.libelle == "Loyer du local"
        assert copie.montant == 800.0
        assert copie.fournisseur == "Mairie de Creil"
        assert copie.mode_paiement == "Virement"
        assert copie.ligne_budget_id == depense_source["ligne_id"]
        # Un même numéro de facture sur deux dépenses est une anomalie comptable.
        assert copie.reference_piece is None


def test_duplication_depense_recopie_la_repartition(app, depense_source):
    """C'est la partie la plus longue à ressaisir, et la plus souvent oubliée."""
    with app.app_context():
        from app.extensions import db
        from app.models import Depense
        from app.services.duplication import dupliquer_depense

        source = db.session.get(Depense, depense_source["id"])
        copie = dupliquer_depense(source)
        db.session.commit()

        reparti = sorted((a.source_type, a.montant) for a in copie.affectations)
        assert reparti == [("fonds_propres", 300.0), ("subvention", 500.0)]
        assert copie.total_affecte == 800.0
        # Et l'original garde exactement les siennes : deux lignes, pas quatre.
        assert len(source.affectations) == 2


def test_duplication_depense_avance_d_un_mois(app, depense_source):
    with app.app_context():
        from app.extensions import db
        from app.models import Depense
        from app.services.duplication import dupliquer_depense

        source = db.session.get(Depense, depense_source["id"])
        copie = dupliquer_depense(source)
        db.session.commit()
        # 31 janvier -> 28 février, et non 3 mars : une charge mensuelle ne
        # doit pas dériver d'un mois sur l'autre au fil de l'année.
        assert copie.date_paiement == date(2026, 2, 28)


def test_mois_suivant_gere_les_fins_de_mois():
    from app.services.duplication import mois_suivant

    assert mois_suivant(date(2026, 1, 31)) == date(2026, 2, 28)
    assert mois_suivant(date(2024, 1, 31)) == date(2024, 2, 29)   # bissextile
    assert mois_suivant(date(2026, 12, 15)) == date(2027, 1, 15)  # passage d'année
    assert mois_suivant(date(2026, 3, 31)) == date(2026, 4, 30)


def test_duplication_depense_sans_date_prend_aujourd_hui(app):
    with app.app_context():
        from app.extensions import db
        from app.models import Depense
        from app.services.duplication import dupliquer_depense

        dep = Depense(libelle="Sans échéance", montant=10.0)
        db.session.add(dep)
        db.session.commit()
        copie = dupliquer_depense(dep)
        db.session.commit()
        assert copie.date_paiement == date.today()


def test_duplication_depense_ne_recopie_pas_les_justificatifs(app, depense_source):
    """La facture de janvier ne prouve pas la dépense de février."""
    with app.app_context():
        from app.extensions import db
        from app.models import Depense, DepenseDocument
        from app.services.duplication import dupliquer_depense

        source = db.session.get(Depense, depense_source["id"])
        db.session.add(DepenseDocument(
            depense_id=source.id, filename="facture.pdf", original_name="facture.pdf",
        ))
        db.session.commit()

        copie = dupliquer_depense(source)
        db.session.commit()
        assert len(copie.documents) == 0
        assert len(source.documents) == 1


def test_route_duplication_depense(admin_client, app, depense_source):
    r = admin_client.post(f"/depense/{depense_source['id']}/dupliquer")
    assert r.status_code == 302
    assert "/depense/" in r.headers["Location"]

    with app.app_context():
        from app.models import Depense

        # La base est partagée par tout le fichier : on cible la copie par sa
        # ligne budgétaire, créée pour ce test seul.
        copies = Depense.query.filter_by(ligne_budget_id=depense_source["ligne_id"]).all()
        assert len(copies) == 2
        nouvelle = [d for d in copies if d.id != depense_source["id"]][0]
        assert nouvelle.date_paiement == date(2026, 2, 28)
        assert len(nouvelle.affectations) == 2
