"""Étape 1 — Notifications automatiques réglables (digest e-mail).

- rien n'est actif par défaut, aucun envoi tant que rien n'est coché ;
- les réglages s'enregistrent depuis la page admin ;
- chaque collecteur produit ses lignes quand il y a matière ;
- l'envoi groupe les sections par destinataire, silence si tout est vide ;
- le déclenchement quotidien ne tourne qu'une fois par jour et n'explose
  jamais (SMTP absent en test).
"""
import uuid
from datetime import date, timedelta

import pytest


@pytest.fixture()
def reglages_vierges(app):
    """Repart de réglages vides avant et après chaque test."""
    from app.extensions import db
    from app.models import NotificationReglage, TachePlanifiee

    with app.app_context():
        NotificationReglage.query.delete()
        TachePlanifiee.query.filter_by(nom="notifications_digest").delete()
        db.session.commit()
    yield
    with app.app_context():
        NotificationReglage.query.delete()
        TachePlanifiee.query.filter_by(nom="notifications_digest").delete()
        db.session.commit()


@pytest.fixture()
def envois_captures(app, monkeypatch):
    """Capture les e-mails au lieu de parler à un vrai serveur SMTP."""
    from app.services import notifications as svc

    boite = []

    def _capture(to, subject, body):
        boite.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(svc, "_envoyer_texte", _capture)
    return boite


# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------

def test_rien_actif_par_defaut(app, reglages_vierges):
    from app.services.notifications import notifications_actives, reglages_effectifs

    with app.app_context():
        assert notifications_actives() is False
        assert all(not r["actif"] for r in reglages_effectifs())


def test_page_admin_et_enregistrement(admin_client, app, reglages_vierges):
    r = admin_client.get("/admin/notifications")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Notifications automatiques" in page
    assert "Échéances financeurs" in page

    r = admin_client.post("/admin/notifications", data={
        "actif_sauvegarde": "1",
        "destinataires_sauvegarde": "direction@exemple.org",
        "frequence_sauvegarde": "quotidien",
        "seuil_sauvegarde": "3",
        "destinataires_impayes": "compta@exemple.org",
        "frequence_impayes": "hebdomadaire",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "enregistrés" in r.get_data(as_text=True)

    with app.app_context():
        from app.services.notifications import reglages_effectifs
        par_code = {x["code"]: x for x in reglages_effectifs()}
        assert par_code["sauvegarde"]["actif"] is True
        assert par_code["sauvegarde"]["destinataires"] == "direction@exemple.org"
        assert par_code["sauvegarde"]["seuil_jours"] == 3
        assert par_code["impayes"]["actif"] is False  # case non cochée


def test_page_refusee_aux_anonymes(client):
    assert client.get("/admin/notifications").status_code in (302, 401, 403)


# ---------------------------------------------------------------------------
# Collecteurs
# ---------------------------------------------------------------------------

def test_collecteur_echeances(app):
    from app.extensions import db
    from app.models import Subvention
    from app.services.notifications import _lignes_echeances_financeurs

    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        sub = Subvention(
            nom=f"Notif-Echeance-{suf}", secteur="Numérique", annee_exercice=2003,
            financeur="CAF Test", montant_attribue=1000, montant_recu=0,
            date_versement_prevu=date.today() - timedelta(days=10),
        )
        db.session.add(sub)
        db.session.commit()
        lignes = _lignes_echeances_financeurs(30, date.today())
        assert any(f"Notif-Echeance-{suf}" in l and "RETARD" in l for l in lignes)


def test_collecteur_impayes(app):
    from app.extensions import db
    from app.models import Cotisation, Participant
    from app.services.cotisations import annee_scolaire_courante
    from app.services.notifications import _lignes_impayes

    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        p = Participant(nom=f"NotifImp{suf}", prenom="Test")
        db.session.add(p)
        db.session.flush()
        db.session.add(Cotisation(
            annee_scolaire=annee_scolaire_courante(),
            type_cotisation="adhesion_individuelle",
            participant_id=p.id, montant_du=15.0, date_reference=date.today(),
        ))
        db.session.commit()
        lignes = _lignes_impayes(None, date.today())
        assert lignes and "non soldée" in lignes[0]


def test_collecteur_sauvegarde(app, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc_sauv
    from app.services.notifications import _lignes_sauvegarde

    monkeypatch.setattr(svc_sauv, "dossier_sauvegardes", lambda: tmp_path)
    with app.app_context():
        # Aucune sauvegarde -> alerte.
        lignes = _lignes_sauvegarde(2, date.today())
        assert lignes and "Aucune sauvegarde" in lignes[0]
        # Sauvegarde fraîche -> silence.
        svc_sauv.creer_sauvegarde()
        assert _lignes_sauvegarde(2, date.today()) == []


def test_collecteur_rappels(app):
    from app.extensions import db
    from app.models import SuiviRappel
    from app.services.notifications import _lignes_rappels

    suf = uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add(SuiviRappel(
            titre=f"Rappel en retard {suf}", categorie="general",
            echeance=date.today() - timedelta(days=4),
        ))
        db.session.commit()
        lignes = _lignes_rappels(None, date.today())
        assert any(suf in l and "4 j de retard" in l for l in lignes)


# ---------------------------------------------------------------------------
# Digest : construction, envoi, fréquences
# ---------------------------------------------------------------------------

def test_digest_silencieux_sans_reglage(app, reglages_vierges, envois_captures):
    from app.services.notifications import envoyer_digest

    with app.app_context():
        resultat = envoyer_digest(forcer=True)
    assert resultat["envoyes"] == 0
    assert envois_captures == []


def test_digest_envoye_et_groupe_par_destinataire(app, reglages_vierges, envois_captures, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc_sauv
    from app.services.notifications import enregistrer_reglage, envoyer_digest

    monkeypatch.setattr(svc_sauv, "dossier_sauvegardes", lambda: tmp_path)  # aucune sauvegarde -> contenu garanti
    with app.app_context():
        enregistrer_reglage("sauvegarde", actif=True, frequence="quotidien",
                            destinataires="a@ex.org, b@ex.org", seuil_jours=2)
        enregistrer_reglage("rappels", actif=True, frequence="quotidien",
                            destinataires="a@ex.org", seuil_jours=None)
        resultat = envoyer_digest(forcer=True)

    assert resultat["envoyes"] == 2  # un e-mail par adresse, pas par type
    par_dest = {e["to"]: e for e in envois_captures}
    assert set(par_dest) == {"a@ex.org", "b@ex.org"}
    assert "Sauvegarde en retard" in par_dest["a@ex.org"]["body"]
    assert "Récapitulatif" in par_dest["a@ex.org"]["subject"]


def test_frequence_hebdomadaire_respectee(app, reglages_vierges, envois_captures, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc_sauv
    from app.services.notifications import construire_digest, enregistrer_reglage

    monkeypatch.setattr(svc_sauv, "dossier_sauvegardes", lambda: tmp_path)
    with app.app_context():
        enregistrer_reglage("sauvegarde", actif=True, frequence="hebdomadaire",
                            destinataires="a@ex.org", seuil_jours=2)
        lundi = date(2026, 7, 13)      # un lundi
        mardi = date(2026, 7, 14)
        assert construire_digest(lundi) != {}
        assert construire_digest(mardi) == {}
        assert construire_digest(mardi, forcer=True) != {}


def test_declenchement_quotidien_une_seule_fois(app, reglages_vierges, envois_captures, tmp_path, monkeypatch):
    from app.models import TachePlanifiee
    from app.services import sauvegarde as svc_sauv
    from app.services.notifications import digest_quotidien_si_necessaire, enregistrer_reglage

    monkeypatch.setattr(svc_sauv, "dossier_sauvegardes", lambda: tmp_path)
    with app.app_context():
        enregistrer_reglage("sauvegarde", actif=True, frequence="quotidien",
                            destinataires="a@ex.org", seuil_jours=2)
        digest_quotidien_si_necessaire()
        premier = len(envois_captures)
        digest_quotidien_si_necessaire()  # même jour : ne renvoie pas
        assert len(envois_captures) == premier == 1
        tache = TachePlanifiee.query.filter_by(nom="notifications_digest").first()
        assert tache is not None and tache.derniere_execution is not None


def test_declenchement_sans_smtp_ne_casse_rien(app, reglages_vierges, tmp_path, monkeypatch):
    """SMTP non configuré : l'échec d'envoi est journalisé, jamais propagé."""
    from app.services import sauvegarde as svc_sauv
    from app.services.notifications import digest_quotidien_si_necessaire, enregistrer_reglage

    monkeypatch.setattr(svc_sauv, "dossier_sauvegardes", lambda: tmp_path)
    with app.app_context():
        enregistrer_reglage("sauvegarde", actif=True, frequence="quotidien",
                            destinataires="a@ex.org", seuil_jours=2)
        digest_quotidien_si_necessaire()  # ne doit pas lever


def test_apercu_depuis_admin(admin_client, app, reglages_vierges, envois_captures, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc_sauv

    monkeypatch.setattr(svc_sauv, "dossier_sauvegardes", lambda: tmp_path)
    with app.app_context():
        from app.services.notifications import enregistrer_reglage
        enregistrer_reglage("sauvegarde", actif=True, frequence="hebdomadaire",
                            destinataires="apercu@ex.org", seuil_jours=2)

    r = admin_client.post("/admin/notifications/apercu", follow_redirects=True)
    assert r.status_code == 200
    assert "Aperçu envoyé" in r.get_data(as_text=True)
    assert envois_captures and envois_captures[0]["to"] == "apercu@ex.org"
