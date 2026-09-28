"""Registre des numéros endommagé ou disparu : émission bloquée (défaut 3).

Reproduit : 42 déjà émis, compteur SQL ramené à 40 par une restauration,
registre principal à 42, copie précédente à 41 ; le registre principal
s'abîme. La reconstruction « copie précédente + compteur SQL » donnait 41 et
le logiciel émettait de nouveau le n° 42.

Désormais : dernier numéro impossible à établir avec certitude → émission
suspendue (marque persistante), fichiers endommagés conservés, rapprochement
par une personne habilitée (Contrôle → Registres), jamais en dessous d'un
maximum connu.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import uuid
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture()
def donnees(app, tmp_path, monkeypatch):
    from test_registres_consolidation import oublier_registre_tenu
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    (tmp_path / "runtime").mkdir()
    oublier_registre_tenu(app)
    return tmp_path / "runtime"


def _espace():
    return f"don:{2100 + int(uuid.uuid4().int % 800)}"


def _etat_du_scenario(app, donnees, espace):
    """42 émis ; copie précédente 41 ; compteur SQL ramené à 40."""
    from app.extensions import db
    from app.models import FinancialSequence
    from app.services.financial_sequence import noter_maxima
    noter_maxima({espace: 41})
    noter_maxima({espace: 42})
    assert json.loads((donnees / "numeros-emis.json.prec").read_text(encoding="utf-8"))[espace] == 41
    with app.app_context():
        ligne = FinancialSequence.query.filter_by(namespace=espace).first()
        if ligne is None:
            db.session.add(FinancialSequence(namespace=espace, value=40))
        else:
            ligne.value = 40
        db.session.commit()


def _abimer(donnees):
    (donnees / "numeros-emis.json").write_text('{"don:', encoding="utf-8")


def test_scenario_exact_40_41_42_ne_reemet_jamais_42(app, donnees):
    from app.extensions import db
    from app.services.financial_sequence import RegistreBloque, next_number
    espace = _espace()
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    with app.app_context():
        with pytest.raises(RegistreBloque) as erreur:
            next_number(espace, [])
        db.session.rollback()
    assert "Contrôle → Registres" in str(erreur.value)
    assert list(donnees.glob("numeros-emis.illisible-*"))          # conservé pour diagnostic
    assert (donnees / "numeros-emis.bloque.json").exists()


def test_emission_refusee_par_l_application_avec_message(app, admin_client, donnees):
    from app.models import Don
    espace = f"don:{__import__('datetime').date.today().year}"
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    with app.app_context():
        avant = Don.query.count()
    r = admin_client.post("/dons/nouveau", data={"donateur_nom": "Bloqué", "montant": "5",
                                                 "date_don": __import__("datetime").date.today().isoformat()},
                          follow_redirects=True)
    assert "suspendue" in r.get_data(as_text=True)
    with app.app_context():
        assert Don.query.count() == avant
    page = admin_client.get("/controle/registres").get_data(as_text=True)
    assert "Émission suspendue" in page and espace in page


def test_registre_disparu_sur_une_installation_existante(app, donnees):
    from app.extensions import db
    from app.services.financial_sequence import RegistreBloque, next_number
    espace = _espace()
    with app.app_context():
        next_number(espace, [])
        db.session.commit()
    (donnees / "numeros-emis.json").unlink()
    with app.app_context():
        with pytest.raises(RegistreBloque, match="disparu"):
            next_number(espace, [])
        db.session.rollback()


def test_registre_et_dossier_runtime_disparus_mais_base_le_sait(app, donnees):
    """Tout le dossier runtime a disparu (autre machine) : la base garde la
    trace qu'un registre était tenu → blocage, pas une « première
    installation »."""
    import shutil
    from app.extensions import db
    from app.services.financial_sequence import RegistreBloque, next_number
    espace = _espace()
    with app.app_context():
        next_number(espace, [])
        db.session.commit()
    shutil.rmtree(donnees)
    donnees.mkdir()
    with app.app_context():
        with pytest.raises(RegistreBloque):
            next_number(espace, [])
        db.session.rollback()


def test_premiere_installation_sans_registre(fresh_app, tmp_path, monkeypatch):
    """Base neuve, aucun registre : ce n'est pas une perte, l'émission marche."""
    from app.extensions import db
    from app.services.financial_sequence import next_number
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    (tmp_path / "runtime").mkdir()
    with fresh_app.app_context():
        assert next_number("don:2030", []) == 1
        db.session.commit()
    assert (tmp_path / "runtime" / "numeros-emis.json").exists()


def test_blocage_persistant_apres_redemarrage(app, donnees):
    """La marque est sur disque : un autre processus (service redémarré)
    la voit et refuse aussi."""
    import subprocess
    espace = _espace()
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    from app.services.financial_sequence import RegistreBloque, lire_registre
    with pytest.raises(RegistreBloque):
        lire_registre()
    script = (
        "import os, sys; sys.path.insert(0, %r); os.environ['APP_DATA_DIR'] = %r\n"
        "from app.services.financial_sequence import noter_maxima, RegistreBloque\n"
        "try:\n    noter_maxima({%r: 99})\nexcept RegistreBloque:\n    sys.exit(3)\n"
    ) % (str(RACINE), str(donnees.parent), espace)
    assert subprocess.run([sys.executable, "-c", script], cwd=RACINE, timeout=120).returncode == 3


def test_retablir_un_maximum_sur_puis_emettre_au_dela(app, admin_client, donnees):
    from app.extensions import db
    from app.models import AuditLog
    from app.services.financial_sequence import next_number
    espace = _espace()
    serie, annee = espace.split(":")
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    with app.app_context():
        from app.services.financial_sequence import blocage
        assert blocage()["bornes"][espace] == 41
    # Sous le maximum connu (41) : refusé, blocage maintenu.
    r = admin_client.post("/controle/registres/retablir", data={f"max_{espace}": "40"}, follow_redirects=True)
    assert "au moins 41" in r.get_data(as_text=True)
    assert (donnees / "numeros-emis.bloque.json").exists()
    # Le papier montre le n° 42 : déclaré (avec les autres séries connues),
    # journalisé, blocage levé.
    with app.app_context():
        from app.services.financial_sequence import blocage as _blocage
        autres = {f"max_{k}": str(v) for k, v in _blocage()["bornes"].items()}
    admin_client.post("/controle/registres/retablir", data={**autres, f"max_{espace}": "42"})
    assert not (donnees / "numeros-emis.bloque.json").exists()
    with app.app_context():
        assert next_number(espace, []) == 43
        db.session.commit()
        assert AuditLog.query.filter_by(action="registres.numeros_retablis").count() >= 1
    # Jamais à la baisse ensuite.
    admin_client.post("/controle/registres/dernier-numero", data={"serie": serie, "annee": annee, "numero": "5"})
    with app.app_context():
        assert next_number(espace, []) == 44
        db.session.rollback()


def test_retablissement_exige_toutes_les_series_connues(app, admin_client, donnees):
    espace, autre = _espace(), f"facture:{2100 + int(uuid.uuid4().int % 800)}"
    from app.services.financial_sequence import noter_maxima
    noter_maxima({autre: 7})
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    r = admin_client.post("/controle/registres/retablir", data={f"max_{espace}": "42"}, follow_redirects=True)
    assert autre in r.get_data(as_text=True)
    assert (donnees / "numeros-emis.bloque.json").exists()


def _processus_emetteur(dossier, espace, depart):
    os.environ["APP_DATA_DIR"] = dossier
    sys.path.insert(0, str(RACINE))
    from app.services.financial_sequence import RegistreBloque, noter_maxima
    depart.wait(30)
    try:
        noter_maxima({espace: 1000})
    except RegistreBloque:
        sys.exit(3)
    sys.exit(0)


def test_processus_concurrents_pendant_le_blocage(app, donnees):
    espace = _espace()
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    contexte = multiprocessing.get_context("spawn")
    depart = contexte.Barrier(4)
    processus = [contexte.Process(target=_processus_emetteur, args=(str(donnees.parent), espace, depart))
                 for _ in range(4)]
    for p in processus:
        p.start()
    for p in processus:
        p.join(120)
    assert [p.exitcode for p in processus] == [3, 3, 3, 3]
    assert len(list(donnees.glob("numeros-emis.illisible-*"))) == 1     # mis de côté une seule fois


def test_restauration_pendant_le_blocage_ne_leve_rien(app, donnees):
    """Une copie des registres fusionnée pendant le blocage (restauration,
    import) relève les bornes connues mais ne lève pas le blocage."""
    from app.services.financial_sequence import blocage, noter_maxima
    espace = _espace()
    _etat_du_scenario(app, donnees, espace)
    _abimer(donnees)
    with app.app_context():
        with pytest.raises(Exception):
            noter_maxima({espace: 50})
        noter_maxima({espace: 50}, pendant_blocage=True)
        assert blocage()["bornes"][espace] == 50


# ---------------------------------------------------------------------------
# Registre des effacements : pas de fausse garantie après une reconstruction
# ---------------------------------------------------------------------------

def test_effacements_registre_endommage_incident_visible_jusqu_a_verification(app, admin_client, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services import registre_effacements as re_
    from app.services.purge_rgpd import anonymiser_participant
    with app.app_context():
        p = Participant(nom="IncidentRgpd", prenom="T", created_secteur="Familles")
        db.session.add(p)
        db.session.commit()
        anonymiser_participant(p)
        db.session.commit()
    (donnees / "registre-effacements.json").write_text("{abîmé", encoding="utf-8")
    with app.app_context():
        re_.lire_fichier()
        assert re_.etat()["incident"]["raison"] == "endommagé"
    page = admin_client.get("/controle/registres").get_data(as_text=True)
    assert "reconstitué" in page
    admin_client.post("/controle/registres/effacements/incident", data={"note": "vérifié : rien de manquant"})
    with app.app_context():
        assert re_.etat()["incident"] is None


def test_effacements_registre_disparu_detecte(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services import registre_effacements as re_
    from app.services.purge_rgpd import anonymiser_participant
    with app.app_context():
        p = Participant(nom="DisparuRgpd", prenom="T", created_secteur="Familles")
        db.session.add(p)
        db.session.commit()
        anonymiser_participant(p)
        db.session.commit()
        pid = p.id
    (donnees / "registre-effacements.json").unlink()
    with app.app_context():
        entrees = re_.lire_fichier()
        assert any(e["participant_id"] == pid for e in entrees.values())
        assert re_.etat()["incident"]["raison"] == "disparu"
