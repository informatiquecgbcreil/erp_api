"""Lot 4 — Bilans financeurs (page rebranchée) + bouton PDF du bilan lourd."""
import uuid


def test_compute_bilans_financeurs(app):
    from app.extensions import db
    from app.models import Subvention, LigneBudget, Depense
    from app.bilans.services import compute_bilans_financeurs, BilansScope

    with app.app_context():
        sub = Subvention(nom=f"Fin {uuid.uuid4().hex[:6]}", secteur="Numérique",
                         annee_exercice=2030, montant_attribue=1000.0, montant_recu=800.0)
        db.session.add(sub)
        db.session.flush()
        ligne = LigneBudget(subvention_id=sub.id, nature="charge", compte="60",
                            libelle="Achat", montant_base=1000.0, montant_reel=600.0)
        db.session.add(ligne)
        db.session.flush()
        db.session.add_all([
            Depense(ligne_budget_id=ligne.id, libelle="Facture A", montant=250.0, statut="valide"),
            Depense(ligne_budget_id=ligne.id, libelle="Facture B", montant=150.0, statut="valide"),
            Depense(ligne_budget_id=ligne.id, libelle="Brouillon", montant=999.0, statut="brouillon"),
        ])
        db.session.commit()

        data = compute_bilans_financeurs(2030, BilansScope(secteurs=None))
        row = next(r for r in data["rows"] if r["id"] == sub.id)
        assert row["accorde"] == 1000.0
        assert row["depenses"] == 400.0      # brouillon exclu
        assert row["reste"] == 600.0
        assert row["taux"] == 40.0
        assert row["nb_factures"] == 2
        assert row["a_ventiler"] == 200.0    # reçu 800 - lignes réelles 600
        assert data["totaux"]["depenses"] >= 400.0


def test_page_financeurs_rendue(admin_client):
    """La page financeurs (ex-template orphelin) est désormais accessible."""
    r = admin_client.get("/bilans/financeurs?year=2030")
    assert r.status_code == 200
    assert "Financeurs" in r.get_data(as_text=True)


def test_bilan_lourd_bouton_pdf(admin_client):
    r = admin_client.get("/bilans/lourds?year=2026")
    assert r.status_code == 200
    assert "Imprimer / PDF" in r.get_data(as_text=True)
