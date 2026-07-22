"""Tests du module Veille financements.

Tout se joue hors réseau : les collecteurs sont testés en monkeypatchant le
téléchargement (``_telecharger``) avec des contenus RSS/HTML/JSON figés.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import VeilleOpportunite, VeilleSource
from app.services import veille_financements as vf


# ---------------------------------------------------------------------------
# Unitaires : scoring et détection de type
# ---------------------------------------------------------------------------

def test_score_profil_association():
    score, mots = vf.calculer_score(
        "Appel à projets jeunesse et éducation populaire dans l'Oise (Hauts-de-France)"
    )
    assert score >= vf.PROFIL_MOTS_CLES["oise"] + vf.PROFIL_MOTS_CLES["education populaire"]
    assert "oise" in mots
    assert "education populaire" in mots
    assert "jeunesse" in mots


def test_score_insensible_accents_et_casse():
    score, mots = vf.calculer_score("ÉDUCATION POPULAIRE et PARENTALITÉ")
    assert "education populaire" in mots
    assert "parentalite" in mots
    assert score > 0


def test_detection_type_dispositif():
    assert vf.detecter_type("Appel à projets 2026 de la CAF") == "aap"
    assert vf.detecter_type("Appel à manifestation d'intérêt FEDER") == "ami"
    assert vf.detecter_type("Appel à candidatures jeunes talents") == "candidature"
    assert vf.detecter_type("Une subvention de fonctionnement") == "subvention"
    assert vf.detecter_type("Réunion publique du conseil") == "autre"


def test_hash_url_normalise():
    assert vf._hash_url("https://Exemple.org/aap/") == vf._hash_url("https://exemple.org/aap")
    assert vf._hash_url("https://exemple.org/aap#section") == vf._hash_url("https://exemple.org/aap")
    assert vf._hash_url("https://exemple.org/a") != vf._hash_url("https://exemple.org/b")


# ---------------------------------------------------------------------------
# Collecteurs (réseau simulé)
# ---------------------------------------------------------------------------

RSS_EXEMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Fondation Test</title>
  <item>
    <title>Appel à projets jeunesse dans l'Oise</title>
    <link>https://fondation.example/aap-jeunesse-oise</link>
    <description><![CDATA[<p>Soutien aux associations d'éducation populaire.</p>]]></description>
    <pubDate>Mon, 20 Jul 2026 08:00:00 +0200</pubDate>
  </item>
  <item>
    <title>Rapport annuel</title>
    <link>https://fondation.example/rapport</link>
    <description>Le bilan de la fondation.</description>
  </item>
</channel></rss>
"""

HTML_EXEMPLE = """<html><body>
  <a href="/aides/appel-a-projets-cohesion-sociale-2026">Appel à projets « Cohésion sociale » 2026</a>
  <a href="/agenda/reunion">Réunion du conseil municipal</a>
  <a href="https://ville.example/subventions-aux-associations">Subventions aux associations : campagne 2026</a>
  <a href="#haut">Appel à projets</a>
  <a href="mailto:x@y.z">appel à projets par courriel</a>
</body></html>
"""


@pytest.fixture()
def contexte(app):
    with app.app_context():
        yield app
        # Nettoyage entre tests : le module ne doit pas polluer les autres.
        VeilleOpportunite.query.delete()
        VeilleSource.query.delete()
        db.session.commit()


def test_collecteur_rss_et_dedoublonnage(contexte, monkeypatch):
    monkeypatch.setattr(vf, "_telecharger", lambda url, **kw: RSS_EXEMPLE.encode("utf-8"))
    source = VeilleSource(nom="Fondation Test", type_source="rss", url="https://fondation.example/rss")
    db.session.add(source)
    db.session.commit()

    items = vf._collecter_rss(source)
    assert len(items) == 2
    assert items[0]["titre"] == "Appel à projets jeunesse dans l'Oise"
    assert items[0]["date_publication"] == date(2026, 7, 20)
    assert "éducation populaire" in items[0]["description"]

    assert vf.enregistrer_items(source, items) == 2
    # Deuxième passage : rien de nouveau, et le statut posé par l'équipe survit.
    opp = VeilleOpportunite.query.filter(VeilleOpportunite.titre.ilike("%Oise%")).one()
    opp.statut = "a_etudier"
    db.session.commit()
    assert vf.enregistrer_items(source, items) == 0
    db.session.refresh(opp)
    assert opp.statut == "a_etudier"
    # Scoring et typage appliqués à l'enregistrement.
    assert opp.score > 0
    assert opp.type_dispositif == "aap"
    assert "oise" in (opp.mots_cles or "")


def test_collecteur_html_liens(contexte, monkeypatch):
    monkeypatch.setattr(vf, "_telecharger", lambda url, **kw: HTML_EXEMPLE.encode("utf-8"))
    source = VeilleSource(nom="Ville Test", type_source="html_liens", url="https://ville.example/")
    db.session.add(source)
    db.session.commit()

    items = vf._collecter_html(source)
    titres = [i["titre"] for i in items]
    # Les deux vrais liens sont retenus, avec résolution des URL relatives...
    assert any("Cohésion sociale" in t for t in titres)
    assert any(i["url"] == "https://ville.example/aides/appel-a-projets-cohesion-sociale-2026" for i in items)
    assert any("Subventions aux associations" in t for t in titres)
    # ... mais pas la réunion, ni les ancres/mailto, ni les libellés trop courts.
    assert not any("Réunion" in t for t in titres)
    assert len(items) == 2


def test_seed_sources_par_defaut_idempotent(contexte):
    ajouts = vf.seed_sources_par_defaut()
    assert ajouts == len(vf.SOURCES_PAR_DEFAUT)
    assert vf.seed_sources_par_defaut() == 0
    # Une source par défaut supprimée revient au prochain seed (comportement voulu).
    VeilleSource.query.filter_by(code_defaut="fondation_de_france").delete()
    db.session.commit()
    assert vf.seed_sources_par_defaut() == 1


def test_rafraichir_isole_les_erreurs(contexte, monkeypatch):
    """Une source qui plante n'empêche pas les autres d'être collectées."""
    ok = VeilleSource(nom="OK", type_source="rss", url="https://ok.example/rss")
    ko = VeilleSource(nom="KO", type_source="rss", url="https://ko.example/rss")
    db.session.add_all([ok, ko])
    db.session.commit()

    def faux_telecharger(url, **kw):
        if "ko.example" in url:
            raise OSError("réseau injoignable")
        return RSS_EXEMPLE.encode("utf-8")

    monkeypatch.setattr(vf, "_telecharger", faux_telecharger)
    # On neutralise le seed pour ne tester que nos deux sources.
    monkeypatch.setattr(vf, "seed_sources_par_defaut", lambda: 0)

    resume = vf.rafraichir_toutes_sources()
    assert resume["sources_ok"] == 1
    assert resume["sources_erreur"] == 1
    assert resume["nouveaux"] == 2
    db.session.refresh(ok)
    db.session.refresh(ko)
    assert ok.dernier_statut == "ok"
    assert ko.dernier_statut == "erreur"
    assert "injoignable" in ko.dernier_message


def test_aides_territoires_exige_une_cle(contexte):
    source = VeilleSource(
        nom="AT", type_source="aides_territoires", url="https://aides-territoires.beta.gouv.fr/api/aids/"
    )
    db.session.add(source)
    db.session.commit()
    with pytest.raises(RuntimeError, match="Clé API"):
        vf._collecter_aides_territoires(source)


def test_veille_est_due(contexte):
    assert vf.veille_est_due() is True  # aucune source : premier passage
    s = VeilleSource(nom="S", type_source="rss", url="https://s.example/rss")
    db.session.add(s)
    db.session.commit()
    assert vf.veille_est_due() is True  # jamais vérifiée
    s.derniere_verification = vf.utcnow()
    db.session.commit()
    assert vf.veille_est_due() is False  # fraîchement vérifiée
    s.derniere_verification = vf.utcnow() - timedelta(days=vf.INTERVALLE_JOURS + 1)
    db.session.commit()
    assert vf.veille_est_due() is True  # les 3 jours sont écoulés


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def test_veille_exige_connexion(client):
    reponse = client.get("/veille-financements/", follow_redirects=False)
    assert reponse.status_code in (302, 401)


def test_pages_veille_accessibles(admin_client, app):
    reponse = admin_client.get("/veille-financements/")
    assert reponse.status_code == 200
    assert "Veille financements".encode("utf-8") in reponse.data

    reponse = admin_client.get("/veille-financements/sources")
    assert reponse.status_code == 200
    assert "Sources surveill".encode("utf-8") in reponse.data
    # Le catalogue par défaut a été semé par la page.
    with app.app_context():
        assert VeilleSource.query.count() >= len(vf.SOURCES_PAR_DEFAUT)


def test_changement_statut_et_gestion_source(admin_client, app):
    with app.app_context():
        source = VeilleSource(nom="Src", type_source="rss", url="https://src.example/rss")
        db.session.add(source)
        db.session.flush()
        opp = VeilleOpportunite(
            source_id=source.id,
            titre="AAP test",
            url="https://src.example/aap",
            url_hash=vf._hash_url("https://src.example/aap"),
        )
        db.session.add(opp)
        db.session.commit()
        opp_id, source_id = opp.id, source.id

    reponse = admin_client.post(
        f"/veille-financements/opportunite/{opp_id}/statut",
        data={"statut": "a_etudier"},
        follow_redirects=False,
    )
    assert reponse.status_code == 302
    with app.app_context():
        assert db.session.get(VeilleOpportunite, opp_id).statut == "a_etudier"

    # Ajout d'une source par le formulaire.
    reponse = admin_client.post(
        "/veille-financements/sources/ajouter",
        data={"nom": "Fondation X", "url": "https://x.example/rss", "type_source": "rss"},
        follow_redirects=False,
    )
    assert reponse.status_code == 302
    with app.app_context():
        assert VeilleSource.query.filter_by(nom="Fondation X").count() == 1

    # Mise en pause puis suppression : l'opportunité trouvée est conservée.
    admin_client.post(
        f"/veille-financements/sources/{source_id}/modifier", data={"action": "basculer"}
    )
    with app.app_context():
        assert db.session.get(VeilleSource, source_id).actif is False
    admin_client.post(
        f"/veille-financements/sources/{source_id}/modifier", data={"action": "supprimer"}
    )
    with app.app_context():
        assert db.session.get(VeilleSource, source_id) is None
        conservee = db.session.get(VeilleOpportunite, opp_id)
        assert conservee is not None
        assert conservee.source_id is None
