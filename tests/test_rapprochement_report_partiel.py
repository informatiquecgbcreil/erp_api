"""Report partiel et somme « à qualifier » historique (défaut 1 après la PR #60).

Scénario reproduit : bulletin familial de 30 € sans titulaire rattaché, un
membre a une fiche et sa cotisation porte déjà un report de 20 € ; la reprise
de la PR #59 a inscrit les 30 € « à qualifier ». Le rapprochement de la PR #60
voit 10 € manquants ; « Somme distincte » de 10 € laissait les 30 € à
qualifier : une fois tout qualifié, la caisse comptait 20 + 30 + 10 = 60 €.

Chaque test va jusqu'au bout du parcours de qualification (écran « À
qualifier », argent « dans la caisse ») et vérifie le total réellement
compté pour ce bulletin.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime

import pytest

from test_reprise_reglements import (ANNEE, _bulletin, _cotisation, _encaissements_du_bulletin, _ligne,
                                     _migration, _personne, _reprise_pr59_puis_correctif, _versement_ancien)

LIBELLE = f"Inscription annuelle {ANNEE}-{ANNEE + 1}"


def _famille(app, *, reporte, modes=("especes",), montant=30.0):
    """Bulletin familial sans titulaire, un membre avec fiche ; ``reporte`` :
    montant déjà reporté (réparti sur ``modes``) sur sa cotisation."""
    from app.extensions import db
    with app.app_context():
        membre = _personne(db)
        c = _cotisation(db, participant=membre)
        if reporte:
            part = round(reporte / len(modes), 2)
            for i, mode in enumerate(modes):
                _versement_ancien(db, c, part if i < len(modes) - 1 else round(reporte - part * (len(modes) - 1), 2),
                                  mode, LIBELLE)
        b = _bulletin(db, montant=montant, membres=[membre])
        db.session.commit()
        _reprise_pr59_puis_correctif(db)
        return b.id, membre.id


def _compte_en_caisse(app, bid, membre_id):
    """Argent de ce bulletin réellement compté (hors sommes hors caisse),
    contre-passations comprises."""
    from app.models import Encaissement
    with app.app_context():
        lignes = Encaissement.query.filter(
            (Encaissement.inscription_annuelle_id == bid) | (Encaissement.participant_id == membre_id)).all()
        return round(sum(e.montant for e in lignes if not e.hors_caisse), 2)


def _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre_id):
    """Ce qu'une personne ferait ensuite : qualifier en espèces, dans la
    caisse, chaque somme encore proposée à l'écran « À qualifier »."""
    from app.models import Encaissement
    from app.services.encaissements import a_qualifier
    with app.app_context():
        proposees = [(e.id, e.montant) for e in a_qualifier()
                     if e.inscription_annuelle_id == bid or e.participant_id == membre_id]
    for eid, montant in proposees:
        admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes",
                                                       "montant_0": f"{montant:.2f}", "caisse": "dans"})
    with app.app_context():
        restantes = [e.id for e in a_qualifier() if e.inscription_annuelle_id == bid]
        assert restantes == [], "des sommes du bulletin restent à qualifier"
        return Encaissement.query.count()


def _decider(admin_client, lid, action, montant=None, note="vérifié sur le cahier d'accueil"):
    donnees = {"action": action, "ligne_id": lid, "note": note}
    if montant is not None:
        donnees["montant"] = montant
    return admin_client.post("/caisse/rapprochement-bulletins", data=donnees, follow_redirects=True)


# ---------------------------------------------------------------------------

def test_scenario_exact_30_dont_20_reportes_et_10_manquants(app, admin_client):
    bid, membre = _famille(app, reporte=20.0)
    with app.app_context():
        ligne = _ligne(bid)
        assert (ligne.classement, ligne.montant_prouve, ligne.montant_ecart) == ("a_rapprocher", 20.0, 10.0)
        assert ligne.encaissement_ancien_id is not None
        lid, ancien = ligne.id, ligne.encaissement_ancien_id
    # La somme historique ne peut pas être qualifiée tant que le bulletin
    # n'est pas rapproché (c'est ainsi qu'on arrivait aux 60 €).
    r = admin_client.post("/caisse/a-qualifier", data={"encaissement_id": ancien, "mode_0": "especes",
                                                       "montant_0": "30", "caisse": "dans"}, follow_redirects=True)
    assert "abord ce bulletin" in r.get_data(as_text=True)
    # Plus que le manquant : refusé (20 déjà reportés sur 30).
    assert "au plus 10.00" in _decider(admin_client, lid, "constater", "11").get_data(as_text=True)
    _decider(admin_client, lid, "constater", "10")
    with app.app_context():
        from app.models import Encaissement
        historique = Encaissement.query.get(ancien)
        assert historique.est_contre_passe                      # annulée, pas supprimée
        [annulation] = historique.contre_passations
        assert f"Rapprochement n° {lid}" in annulation.motif and "20.00" in annulation.motif
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) == 30.0          # 20 reportés + 10 constatés


def test_report_integral_rien_de_plus(app, admin_client):
    bid, membre = _famille(app, reporte=30.0)
    with app.app_context():
        ligne = _ligne(bid)
        assert (ligne.classement, ligne.montant_ecart) == ("a_rapprocher", 0.0)
        lid = ligne.id
    assert "rien ne manque" in _decider(admin_client, lid, "constater", "1").get_data(as_text=True)
    _decider(admin_client, lid, "confirmer")
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) == 30.0


def test_aucun_report_la_somme_historique_suffit(app, admin_client):
    bid, membre = _famille(app, reporte=0.0)
    with app.app_context():
        assert _ligne(bid).classement == "suivi"
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) == 30.0


def test_plusieurs_modes_de_paiement(app, admin_client):
    bid, membre = _famille(app, reporte=20.0, modes=("especes", "cheque"))
    with app.app_context():
        lid = _ligne(bid).id
    _decider(admin_client, lid, "constater", "10")
    with app.app_context():
        from app.models import Encaissement
        [constate] = Encaissement.query.filter_by(source_ancienne=f"rapprochement:{lid}").all()
        eid = constate.id
    # Les 10 € manquants payés en deux modes.
    admin_client.post("/caisse/a-qualifier", data={"encaissement_id": eid, "mode_0": "especes", "montant_0": "4",
                                                   "mode_1": "carte", "montant_1": "6", "caisse": "dans"})
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) == 30.0
    with app.app_context():
        from app.models import Encaissement
        modes = {e.mode for e in Encaissement.query.filter(Encaissement.participant_id == membre).all()}
        assert {"especes", "cheque"} <= modes


def test_somme_historique_deja_qualifiee_presentee_pour_verification(app, admin_client):
    """La somme historique a été qualifiée avant le rapprochement (argent déjà
    compté) : impossible de départager seul, aucune décision automatique."""
    from app.extensions import db
    from app.services.encaissements import qualifier
    from app.services.reprise_reglements import classer_tout
    with app.app_context():
        membre = _personne(db)
        c = _cotisation(db, participant=membre)
        _versement_ancien(db, c, 20.0, "especes", LIBELLE)
        b = _bulletin(db, montant=30.0, membres=[membre])
        db.session.commit()
        _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())
        db.session.commit()
        [ancien] = _encaissements_du_bulletin(b.id)
        qualifier(ancien, [("especes", 30.0)], dans_caisse=True)
        db.session.commit()
        classer_tout(db.session.connection())
        db.session.commit()
        ligne = _ligne(b.id)
        assert ligne.classement == "a_rapprocher" and "déjà qualifiée" in ligne.motif
        lid, bid, mid = ligne.id, b.id, membre.id
    avant = _compte_en_caisse(app, bid, mid)
    for action in ("constater", "confirmer"):
        r = _decider(admin_client, lid, action, "10")
        assert "vérification" in r.get_data(as_text=True)
    assert _compte_en_caisse(app, bid, mid) == avant
    r = _decider(admin_client, lid, "verifier", note="contre-passé à la main le 02/10, voir bordereau")
    with app.app_context():
        assert _ligne(bid).decision == "verifie_manuellement"


def test_somme_historique_deja_annulee(app, admin_client):
    from app.extensions import db
    from app.services.encaissements import contre_passer
    bid, membre = _famille(app, reporte=20.0)
    with app.app_context():
        from app.models import Encaissement
        ligne = _ligne(bid)
        contre_passer(db.session.get(Encaissement, ligne.encaissement_ancien_id), "annulée à la main")
        db.session.commit()
        lid = ligne.id
    _decider(admin_client, lid, "constater", "10")
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) == 30.0


def test_double_soumission_et_decisions_simultanees(app, admin_client):
    from sqlalchemy.exc import OperationalError
    from app.extensions import db
    from app.services import rapprochement_reglements as rr
    from app.services.encaissements import DejaEnregistre
    bid, membre = _famille(app, reporte=20.0)
    with app.app_context():
        lid = _ligne(bid).id
    depart = threading.Barrier(2)
    resultats = []

    def decider(action):
        with app.app_context():
            try:
                depart.wait(10)
                if action == "constater":
                    rr.constater_encaissement(lid, "10", note="A")
                else:
                    rr.confirmer_report(lid, note="personne B")
                db.session.commit()
                resultats.append("ok")
            except (DejaEnregistre, OperationalError) as exc:
                db.session.rollback()
                resultats.append(type(exc).__name__)
            finally:
                db.session.remove()

    fils = [threading.Thread(target=decider, args=(a,)) for a in ("constater", "confirmer")]
    for f in fils:
        f.start()
    for f in fils:
        f.join(60)
    assert resultats.count("ok") == 1, resultats
    assert "déjà été rapproché" in _decider(admin_client, lid, "constater", "10").get_data(as_text=True)
    with app.app_context():
        from app.models import Encaissement
        ancien = Encaissement.query.get(_ligne(bid).encaissement_ancien_id)
        assert len(ancien.contre_passations) == 1
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) in (20.0, 30.0)  # B : « déjà reporté » ; A : 20 + 10


def test_installation_ayant_deja_recu_la_pr60(app, admin_client):
    """Rapprochement déjà fait avec la PR #60 (10 € constatés, 30 € historiques
    laissés à qualifier) : la migration corrective le signale, la correction
    annule la somme historique en doublon, jamais sans une personne."""
    from app.extensions import db
    from app.models import Encaissement, RapprochementBulletin
    from sqlalchemy import text
    bid, membre = _famille(app, reporte=20.0)
    with app.app_context():
        ligne = _ligne(bid)
        lid, ancien = ligne.id, ligne.encaissement_ancien_id
        # Décision telle que la PR #60 l'enregistrait : 10 € créés, rien d'autre.
        e = Encaissement(montant=10.0, mode="inconnu", date_encaissement=ligne.date_origine,
                         inscription_annuelle_id=bid, a_qualifier=True, hors_caisse=True,
                         source_ancienne=f"rapprochement:{lid}")
        db.session.add(e)
        db.session.flush()
        db.session.execute(text("UPDATE rapprochement_bulletin SET decision='encaissement_constate', "
                                "decision_montant=10, encaissement_id=:e WHERE id=:l"), {"e": e.id, "l": lid})
        db.session.commit()
        from app.services.reprise_reglements import controler_decisions
        assert controler_decisions(db.session.connection()) >= 1
        db.session.commit()
        assert db.session.get(RapprochementBulletin, lid).controle == "doublon_a_annuler"
        assert not db.session.get(Encaissement, ancien).est_contre_passe     # rien d'automatique
    page = admin_client.get("/caisse/rapprochement-bulletins").get_data(as_text=True)
    assert "À corriger" in page
    admin_client.post("/caisse/rapprochement-bulletins", data={"action": "corriger", "ligne_id": lid,
                                                               "note": "doublon confirmé"})
    admin_client.post("/caisse/rapprochement-bulletins", data={"action": "corriger", "ligne_id": lid,
                                                               "note": "second clic"})
    with app.app_context():
        assert len(db.session.get(Encaissement, ancien).contre_passations) == 1
        assert db.session.get(RapprochementBulletin, lid).controle == "corrige"
    _qualifier_tout_dans_la_caisse(app, admin_client, bid, membre)
    assert _compte_en_caisse(app, bid, membre) == 30.0


def test_reproduction_du_defaut_60_euros(app):
    """Le parcours exact du défaut, par les services : « Somme distincte » de
    10 €, puis qualification dans la caisse de tout ce qui reste à qualifier."""
    from app.extensions import db
    from app.models import Encaissement
    from app.services import rapprochement_reglements as rr
    from app.services.encaissements import a_qualifier, qualifier
    bid, membre = _famille(app, reporte=20.0)
    with app.app_context():
        rr.constater_encaissement(_ligne(bid).id, "10", note="cahier")
        db.session.commit()
        for e in [e for e in a_qualifier() if e.inscription_annuelle_id == bid]:
            qualifier(e, [("especes", e.montant)], dans_caisse=True)
            db.session.commit()
    assert _compte_en_caisse(app, bid, membre) == 30.0


def test_mise_a_jour_d_une_base_au_schema_de_la_pr60(fresh_app):
    """Base au schéma exact de la PR #60 (d2e4f6a8b013) où la décision a déjà
    été prise : la montée de version signale la ligne sans rien écrire en
    caisse."""
    from flask_migrate import downgrade, upgrade
    from sqlalchemy import text
    from app.extensions import db
    from app.services.reprise_reglements import classer_tout
    with fresh_app.app_context():
        downgrade(revision="d2e4f6a8b013")
        membre = _personne(db)
        c = _cotisation(db, participant=membre)
        _versement_ancien(db, c, 20.0, "especes", LIBELLE)
        b = _bulletin(db, montant=30.0, membres=[membre])
        db.session.commit()
        _migration("b5d8e3a1f264").reprendre_donnees(db.session.connection())
        classer_tout(db.session.connection())
        db.session.commit()
        bid = b.id
        lid = db.session.execute(text("SELECT id FROM rapprochement_bulletin WHERE cle = :c"),
                                 {"c": f"bulletin:{bid}"}).scalar_one()
        db.session.execute(text(
            "INSERT INTO encaissement (montant, mode, date_encaissement, inscription_annuelle_id, a_qualifier, "
            "hors_caisse, source_ancienne, created_at) VALUES (10, 'inconnu', :d, :b, :v, :v, :s, :t)"),
            {"d": date(2031, 9, 12), "t": datetime(2031, 9, 12), "b": bid, "v": True, "s": f"rapprochement:{lid}"})
        db.session.execute(text("UPDATE rapprochement_bulletin SET decision = 'encaissement_constate', "
                                "decision_montant = 10 WHERE id = :l"), {"l": lid})
        db.session.commit()
        avant = db.session.execute(text("SELECT id, montant, a_qualifier FROM encaissement ORDER BY id")).fetchall()
        db.session.remove()
        upgrade(revision="head")
        assert db.session.execute(text("SELECT controle FROM rapprochement_bulletin WHERE id = :l"),
                                  {"l": lid}).scalar_one() == "doublon_a_annuler"
        apres = db.session.execute(text("SELECT id, montant, a_qualifier FROM encaissement ORDER BY id")).fetchall()
        assert apres == avant
        db.session.remove()
        downgrade(revision="d2e4f6a8b013")               # le retour arrière se défait proprement
        upgrade(revision="head")
