"""Registres hors base : numéros émis (défaut C) et effacements RGPD (défaut B).

Chaque test travaille dans un dossier de données jetable (``APP_DATA_DIR``
propre au test) : aucun registre réel, aucune base réelle.

Concurrence : barrières et événements, pas de temporisation hasardeuse. Les
seuls délais sont des plafonds (« au plus N secondes ») qui ne décident
jamais du résultat d'un test réussi.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from app.utils.dates import utcnow

RACINE = Path(__file__).resolve().parents[1]


def oublier_registre_tenu(app):
    """La base de test est partagée : effacer la trace « registre des numéros
    tenu » laissée par d'autres tests, pour que ce dossier vierge soit une
    première installation (et non un registre disparu, qui bloque)."""
    from app.extensions import db
    from app.models import TachePlanifiee
    from app.services.financial_sequence import TACHE_TENU
    with app.app_context():
        TachePlanifiee.query.filter_by(nom=TACHE_TENU).delete()
        db.session.commit()


@pytest.fixture()
def donnees(app, tmp_path, monkeypatch):
    """Dossier de données propre au test (registres vides, première installation)."""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    (tmp_path / "runtime").mkdir()
    oublier_registre_tenu(app)
    return tmp_path / "runtime"


def _espace():
    return f"test-{uuid.uuid4().hex[:8]}"


# ===========================================================================
# Défaut C — registre des numéros émis
# ===========================================================================

def test_c_ecritures_concurrentes_ne_perdent_aucun_numero(app, donnees, monkeypatch):
    """Reproduction du défaut : A lit le registre, B lit le même état, A écrit
    le reçu 42, B écrit la facture 17 → le reçu 42 disparaissait. Le verrou
    oblige B à attendre la fin de A : les deux maxima restent."""
    from app.services import financial_sequence as fs
    from app.services import registre_externe as reg

    ecrire_original = reg.ecrire
    dans_ecriture, reprendre = threading.Event(), threading.Event()
    premier = {"fait": False}
    garde = threading.Lock()

    def ecrire_bloquant(chemin, contenu):
        with garde:
            bloquer = not premier["fait"]
            premier["fait"] = True
        if bloquer:
            dans_ecriture.set()
            assert reprendre.wait(10)
        return ecrire_original(chemin, contenu)

    monkeypatch.setattr(reg, "ecrire", ecrire_bloquant)
    erreurs = []

    def ecrivain(maxima):
        try:
            fs.noter_maxima(maxima)
        except Exception as exc:  # noqa: BLE001
            erreurs.append(exc)

    a = threading.Thread(target=ecrivain, args=({"recu:2026": 42},))
    a.start()
    assert dans_ecriture.wait(10)            # A a lu et s'apprête à écrire
    b = threading.Thread(target=ecrivain, args=({"facture:2026": 17},))
    b.start()
    b.join(1.0)                              # sans verrou, B finirait ici
    reprendre.set()
    a.join(10), b.join(10)
    assert not erreurs
    registre = json.loads((donnees / "numeros-emis.json").read_text(encoding="utf-8"))
    assert registre == {"facture:2026": 17, "recu:2026": 42}


def test_c_meme_espace_en_parallele_garde_le_maximum(app, donnees):
    from app.services import financial_sequence as fs
    depart = threading.Barrier(8)

    def ecrivain(valeurs):
        depart.wait()
        for v in valeurs:
            fs.noter_maxima({"don:2026": v})

    fils = [threading.Thread(target=ecrivain, args=(range(i, 200, 8),)) for i in range(8)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(30)
    assert fs.lire_registre() == {"don:2026": 199}


def _processus_ecrivain(dossier, espace_propre, depart, nombre):
    os.environ["APP_DATA_DIR"] = dossier
    sys.path.insert(0, str(RACINE))
    from app.services.financial_sequence import noter_maxima
    depart.wait(30)
    for i in range(1, nombre + 1):
        noter_maxima({espace_propre: i, "commun": i})


def test_c_processus_independants(donnees):
    """Plusieurs processus (service web, sauvegarde, maintenance) écrivent en
    même temps : aucune famille de numéros ne disparaît."""
    contexte = multiprocessing.get_context("spawn")
    depart = contexte.Barrier(4)
    processus = [contexte.Process(target=_processus_ecrivain,
                                  args=(str(donnees.parent), f"espace-{i}", depart, 40 + i))
                 for i in range(4)]
    for p in processus:
        p.start()
    for p in processus:
        p.join(120)
        assert p.exitcode == 0
    registre = json.loads((donnees / "numeros-emis.json").read_text(encoding="utf-8"))
    assert registre == {"commun": 43, "espace-0": 40, "espace-1": 41, "espace-2": 42, "espace-3": 43}


def test_c_ancien_maximum_superieur_a_la_base_restauree(app, donnees):
    from app.extensions import db
    from app.models import FinancialSequence
    from app.services.financial_sequence import next_number
    espace = _espace()
    with app.app_context():
        assert next_number(espace, []) == 1
        assert next_number(espace, []) == 2
        db.session.commit()
        FinancialSequence.query.filter_by(namespace=espace).delete()  # base « restaurée »
        db.session.commit()
        assert next_number(espace, []) == 3
        db.session.commit()


def test_c_attribution_sql_annulee_laisse_le_numero_consomme(app, donnees):
    """Politique de numérotation : un numéro réservé puis abandonné reste
    consommé (trou), il n'est jamais redonné."""
    from app.extensions import db
    from app.services.financial_sequence import lire_registre, next_number
    espace = _espace()
    with app.app_context():
        assert next_number(espace, []) == 1
        db.session.rollback()
        assert lire_registre()[espace] == 1
        assert next_number(espace, []) == 2
        db.session.commit()


def test_c_premiere_installation_sans_fichier(app, donnees):
    from app.extensions import db
    from app.services.financial_sequence import next_number
    assert not (donnees / "numeros-emis.json").exists()
    with app.app_context():
        espace = _espace()
        assert next_number(espace, []) == 1
        db.session.commit()
    assert json.loads((donnees / "numeros-emis.json").read_text(encoding="utf-8"))[espace] == 1


def test_c_ancien_format_repris_tel_quel(app, donnees):
    from app.extensions import db
    from app.services.financial_sequence import next_number
    espace = _espace()
    (donnees / "numeros-emis.json").write_text(json.dumps({espace: 41, "facture:2025": 9}), encoding="utf-8")
    with app.app_context():
        assert next_number(espace, []) == 42
        db.session.commit()
    registre = json.loads((donnees / "numeros-emis.json").read_text(encoding="utf-8"))
    assert registre == {espace: 42, "facture:2025": 9}


def test_c_fichier_endommage_mis_de_cote_et_emission_suspendue(app, donnees):
    """Illisible ≠ vide : le fichier est conservé à part. Depuis la
    consolidation après la PR #60, la copie précédente et les compteurs de la
    base ne servent plus qu'à donner des bornes basses : l'émission est
    suspendue jusqu'au rétablissement (voir test_registre_numeros_blocage)."""
    from app.extensions import db
    from app.services.financial_sequence import RegistreBloque, blocage, lire_registre, next_number, noter_maxima
    from app.services.registre_externe import registres_mis_de_cote
    espace = _espace()
    noter_maxima({"hors-base:2026": 70})
    noter_maxima({"hors-base:2026": 80})      # copie précédente : 70
    with app.app_context():
        assert next_number(espace, []) == 1
        db.session.commit()
        (donnees / "numeros-emis.json").write_text('{"hors-base:2026": 80, "tron', encoding="utf-8")
        with pytest.raises(RegistreBloque):
            lire_registre()
        bornes = blocage()["bornes"]
    assert bornes["hors-base:2026"] == 80 and bornes[espace] == 1   # copie précédente (80) et base (1)
    assert len(registres_mis_de_cote()) == 1


def test_c_fichier_inaccessible_refuse_l_emission(app, donnees, monkeypatch):
    from app.extensions import db
    from app.services import registre_externe as reg
    from app.services.financial_sequence import next_number, noter_maxima
    from app.services.registre_externe import RegistreErreur
    noter_maxima({"x": 1})
    lire_original = Path.read_bytes

    def refuse(self):
        if self.name == "numeros-emis.json":
            raise PermissionError("accès refusé")
        return lire_original(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with app.app_context():
        with pytest.raises(RegistreErreur):
            next_number(_espace(), [])
        db.session.rollback()
    assert reg.registres_mis_de_cote() == []  # rien n'a été « reconstitué » à tort


def test_c_emission_refusee_par_l_application_si_registre_indisponible(app, admin_client, donnees, monkeypatch):
    """Parcours réel : un don dont le numéro ne peut pas être protégé n'est
    pas enregistré ; l'utilisateur reçoit un message compréhensible."""
    from app.models import Don
    from app.services import registre_externe as reg

    def panne(chemin, contenu):
        raise reg.RegistreErreur("Le registre de l'installation n'a pas pu être enregistré.", "test")

    monkeypatch.setattr(reg, "ecrire", panne)
    with app.app_context():
        avant = Don.query.count()
    reponse = admin_client.post("/dons/nouveau", data={
        "donateur_nom": "Registre Test", "montant": "15", "mode": "especes",
        "date_don": utcnow().date().isoformat(), "jeton": uuid.uuid4().hex}, follow_redirects=True)
    assert "pas pu être enregistré" in reponse.get_data(as_text=True)
    with app.app_context():
        assert Don.query.count() == avant


def test_c_temporaire_residuel_et_arret_pendant_l_ecriture(app, donnees):
    """Un processus tué entre l'écriture du temporaire et le remplacement
    laisse l'ancienne version intacte et un résidu, nettoyé au passage
    suivant."""
    from app.services.financial_sequence import lire_registre, noter_maxima
    noter_maxima({"don:2026": 5})
    script = (
        "import os, sys; sys.path.insert(0, %r); os.environ['APP_DATA_DIR'] = %r\n"
        "import app.services.registre_externe as reg\n"
        "def coupure(*a, **k): os._exit(9)\n"
        "reg._remplacer = coupure\n"
        "from app.services.financial_sequence import noter_maxima\n"
        "noter_maxima({'don:2026': 6})\n"
    ) % (str(RACINE), str(donnees.parent))
    resultat = subprocess.run([sys.executable, "-c", script], cwd=RACINE, timeout=120)
    assert resultat.returncode == 9
    assert list(donnees.glob("numeros-emis.json.*.tmp")), "le résidu de l'écriture interrompue est resté"
    assert lire_registre() == {"don:2026": 5}          # ancienne version intacte
    assert not list(donnees.glob("numeros-emis.json.*.tmp"))
    noter_maxima({"don:2026": 6})
    assert lire_registre() == {"don:2026": 6}


def test_c_verrou_borne(donnees):
    from app.services import registre_externe as reg
    chemin = donnees / "numeros-emis.json"
    tenu, liberer = threading.Event(), threading.Event()

    def detenteur():
        with reg.verrou(chemin):
            tenu.set()
            liberer.wait(20)

    fil = threading.Thread(target=detenteur)
    fil.start()
    assert tenu.wait(10)
    try:
        with pytest.raises(reg.RegistreVerrouille):
            with reg.verrou(chemin, delai=0.2):
                pass
    finally:
        liberer.set()
        fil.join(10)
    with reg.verrou(chemin, delai=1):
        pass


def test_c_verrou_entre_processus(donnees):
    """Le verrou système tient contre un autre processus (pas seulement les
    fils du même processus)."""
    from app.services import registre_externe as reg
    chemin = donnees / "numeros-emis.json"
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from pathlib import Path\n"
        "import app.services.registre_externe as reg\n"
        "try:\n"
        "    with reg.verrou(Path(%r), delai=0.3):\n"
        "        sys.exit(0)\n"
        "except reg.RegistreVerrouille:\n"
        "    sys.exit(3)\n"
    ) % (str(RACINE), str(chemin))
    with reg.verrou(chemin):
        assert subprocess.run([sys.executable, "-c", script], cwd=RACINE, timeout=120).returncode == 3
    assert subprocess.run([sys.executable, "-c", script], cwd=RACINE, timeout=120).returncode == 0


# ===========================================================================
# Défaut B — registre des effacements
# ===========================================================================

def _fiche(app, **extra):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        moment = utcnow() - timedelta(days=5000)
        p = Participant(nom=f"Eff{uuid.uuid4().hex[:8]}", prenom="Test", created_secteur="Familles",
                        created_at=moment, updated_at=moment, **extra)
        db.session.add(p)
        db.session.commit()
        _CREATION[p.id] = p.created_at
        return p.id


#: SQLite réattribue le numéro d'une fiche supprimée : les tests désignent une
#: fiche par son numéro ET sa date de création, comme le registre.
_CREATION: dict = {}


def _entrees_du_fichier(donnees, pid):
    fichier = donnees / "registre-effacements.json"
    if not fichier.exists():
        return []
    cree = _CREATION.get(pid)
    return [e for e in json.loads(fichier.read_text(encoding="utf-8"))["entrees"].values()
            if e["participant_id"] == pid
            and e["participant_cree_le"] == (cree.isoformat(timespec="microseconds") if cree else None)]


def _lignes(app, pid):
    from app.models import EffacementRgpd
    with app.app_context():
        return EffacementRgpd.query.filter_by(participant_id=pid, participant_cree_le=_CREATION.get(pid)).all()


def _restaurer_identite(app, pid, nom="Revenue"):
    """Simule la restauration d'une sauvegarde d'avant l'effacement."""
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        p = db.session.get(Participant, pid)
        p.nom, p.prenom = nom, "Restaurée"
        db.session.commit()


def test_b_anonymisation_annulee_jamais_reappliquee(app, donnees):
    """Reproduction du défaut : anonymiser, annuler (rollback), puis
    réappliquer le registre après restauration → la fiche devenait ANONYME."""
    from app.extensions import db
    from app.models import Participant
    from app.services.purge_rgpd import anonymiser_participant
    from app.services.registre_effacements import reappliquer
    pid = _fiche(app)
    with app.app_context():
        nom = db.session.get(Participant, pid).nom
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.rollback()
        assert db.session.get(Participant, pid).nom == nom
    assert _entrees_du_fichier(donnees, pid) == []
    assert _lignes(app, pid) == []
    with app.app_context():
        assert pid not in reappliquer()
        assert db.session.get(Participant, pid).nom == nom


def test_b_anonymisation_validee_protegee_et_reappliquee(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services.purge_rgpd import anonymiser_participant
    from app.services.registre_effacements import reappliquer
    pid = _fiche(app)
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
    [entree] = _entrees_du_fichier(donnees, pid)
    assert entree["etat"] == "confirme" and entree["nature"] == "anonymisation"
    assert all(ligne.exporte_le is not None for ligne in _lignes(app, pid))
    _restaurer_identite(app, pid)
    with app.app_context():
        assert reappliquer() == [pid]
        assert db.session.get(Participant, pid).nom == "ANONYME"
        # Répété : sans effet supplémentaire (ni fiche, ni ligne, ni journal).
        from app.models import AuditLog
        journal_avant = AuditLog.query.count()
        assert reappliquer() == []
        assert AuditLog.query.count() == journal_avant
    assert len(_lignes(app, pid)) == 1 and len(_entrees_du_fichier(donnees, pid)) == 1


def test_b_suppression_definitive_validee_et_annulee(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services.participant_suppression import supprimer_definitivement
    annulee, validee = _fiche(app), _fiche(app)
    with app.app_context():
        supprimer_definitivement(db.session.get(Participant, annulee))
        db.session.rollback()
        assert db.session.get(Participant, annulee).nom.startswith("Eff")
        supprimer_definitivement(db.session.get(Participant, validee))
        db.session.commit()
        assert db.session.get(Participant, validee) is None
    assert _entrees_du_fichier(donnees, annulee) == [] and _lignes(app, annulee) == []
    natures = {e["nature"] for e in _entrees_du_fichier(donnees, validee)}
    assert "suppression" in natures


def test_b_point_de_sauvegarde_valide_puis_transaction_annulee(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services.purge_rgpd import anonymiser_participant
    pid = _fiche(app)
    with app.app_context():
        with db.session.begin_nested():
            anonymiser_participant(db.session.get(Participant, pid))
        # Le point de sauvegarde est validé… mais pas la transaction.
        db.session.rollback()
        assert db.session.get(Participant, pid).nom != "ANONYME"
    assert _entrees_du_fichier(donnees, pid) == [] and _lignes(app, pid) == []


def test_b_fiche_en_echec_dans_une_purge_par_lots(app, donnees, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import audit, purge_rgpd
    bonne, mauvaise = _fiche(app), _fiche(app)
    enregistrer = audit.enregistrer

    def journal_en_panne(action, **kw):
        if kw.get("participant_id") == mauvaise:
            raise RuntimeError("panne simulée sur une fiche")
        return enregistrer(action, **kw)

    monkeypatch.setattr(audit, "enregistrer", journal_en_panne)
    monkeypatch.setattr(purge_rgpd, "participants_inactifs", lambda annees=None: [
        {"participant": db.session.get(Participant, pid), "derniere_activite": utcnow() - timedelta(days=4000)}
        for pid in (bonne, mauvaise)])
    with app.app_context():
        rapport = purge_rgpd.purger_par_lots(declenchement="test")
        assert rapport["echecs"] == [mauvaise]
        assert db.session.get(Participant, mauvaise).nom != "ANONYME"
    assert _lignes(app, mauvaise) == [] and _entrees_du_fichier(donnees, mauvaise) == []
    assert len(_entrees_du_fichier(donnees, bonne)) == 1


def test_b_panne_du_registre_visible_et_rattrapee(app, donnees, monkeypatch):
    """Échec d'écriture : l'effacement est fait, sa protection hors base est
    « en attente » (visible), rien n'est masqué ; un redémarrage la termine."""
    from app.extensions import db
    from app.models import Participant
    from app.services import registre_effacements as re_
    from app.services import registre_externe as reg
    from app.services.purge_rgpd import anonymiser_participant
    pid = _fiche(app)
    ecrire = reg.ecrire

    def panne(chemin, contenu):
        raise reg.RegistreErreur("disque plein", "test")

    monkeypatch.setattr(reg, "ecrire", panne)
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()                                   # l'effacement est validé
        assert db.session.get(Participant, pid).nom == "ANONYME"
        assert re_.etat()["en_attente"] >= 1
        with pytest.raises(reg.RegistreErreur):
            re_.exporter_en_attente()
    assert _entrees_du_fichier(donnees, pid) == []
    monkeypatch.setattr(reg, "ecrire", ecrire)
    with app.app_context():
        re_.synchroniser()                                    # « redémarrage »
        assert re_.etat()["en_attente"] == 0
    assert len(_entrees_du_fichier(donnees, pid)) == 1


def test_b_interruption_entre_fichier_et_marquage(app, donnees):
    """Arrêt après l'écriture du fichier mais avant le marquage « exporté » :
    la recopie suivante réécrit la même entrée, sans doublon."""
    from app.extensions import db
    from app.models import EffacementRgpd, Participant
    from app.services import registre_effacements as re_
    from app.services.purge_rgpd import anonymiser_participant
    pid = _fiche(app)
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        ligne = EffacementRgpd.query.filter_by(participant_id=pid).one()
        ligne.exporte_le = None                               # marquage perdu
        db.session.commit()
        assert re_.exporter_en_attente() >= 1
        assert EffacementRgpd.query.filter_by(participant_id=pid).one().exporte_le is not None
    assert len(_entrees_du_fichier(donnees, pid)) == 1


def test_b_identifiant_reutilise_par_une_autre_personne(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services.purge_rgpd import anonymiser_participant
    from app.services.registre_effacements import reappliquer
    pid = _fiche(app)
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        # Même numéro, autre personne (séquence revenue en arrière après une
        # restauration PostgreSQL, ou numéro réutilisé par SQLite).
        p = db.session.get(Participant, pid)
        p.nom, p.prenom, p.created_at = "Autre", "Personne", utcnow()
        db.session.commit()
        assert pid not in reappliquer()
        assert db.session.get(Participant, pid).nom == "Autre"


def test_b_fiche_ancienne_sans_date_de_creation(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services.purge_rgpd import anonymiser_participant
    from app.services.registre_effacements import reappliquer
    pid = _fiche(app)
    with app.app_context():
        p = db.session.get(Participant, pid)
        p.created_at = None
        db.session.commit()
        anonymiser_participant(p)
        db.session.commit()
    _restaurer_identite(app, pid)
    with app.app_context():
        assert pid in reappliquer()
        p = db.session.get(Participant, pid)
        p.nom, p.created_at = "Nouvelle", utcnow()           # une fiche créée depuis a une date
        db.session.commit()
        assert pid not in reappliquer()


def test_b_ancien_registre_classe_sans_rejeu_aveugle(app, admin_client, donnees):
    """L'ancien fichier (écrit avant validation) n'est jamais réappliqué à
    l'aveugle : une fiche encore identifiée passe « à vérifier »."""
    from app.extensions import db
    from app.models import EffacementRgpd, Participant
    from app.services import registre_effacements as re_
    from app.services.purge_rgpd import anonymiser_participant
    intacte, anonyme, absente = _fiche(app), _fiche(app), 10_000_000 + int(uuid.uuid4().int % 1_000_000)
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, anonyme))
        db.session.commit()
        fiches = {pid: db.session.get(Participant, pid) for pid in (intacte, anonyme)}
        ancien = {"anonymises": [
            {"id": pid, "cree_le": fiches[pid].created_at.isoformat(timespec="seconds")} for pid in (intacte, anonyme)
        ] + [{"id": absente, "cree_le": "2019-01-01T10:00:00"}], "supprimes": []}
    contenu = json.dumps(ancien).encode("utf-8")
    (donnees / "effacements.json").write_bytes(contenu)
    with app.app_context():
        nom = db.session.get(Participant, intacte).nom
        re_.reappliquer()
        re_.reappliquer()                                    # idempotent
        assert db.session.get(Participant, intacte).nom == nom   # pas rejoué
        etats = {ligne.participant_id: ligne.etat for ligne in EffacementRgpd.query.filter(
            EffacementRgpd.origine == "ancien_registre",
            EffacementRgpd.participant_id.in_([intacte, anonyme, absente])).all()}
        assert etats == {intacte: "a_verifier", anonyme: "confirme", absente: "confirme"}
        a_verifier = EffacementRgpd.query.filter_by(participant_id=intacte, etat="a_verifier").one()
    assert (donnees / "effacements.json").read_bytes() == contenu  # jamais modifié

    page = admin_client.get("/controle/registres")
    assert page.status_code == 200 and "à vérifier" in page.get_data(as_text=True)
    # Décision humaine, puis double envoi : une seule application.
    r = admin_client.post(f"/controle/registres/effacements/{a_verifier.id}", data={"decision": "confirmer"})
    assert r.status_code == 302
    r = admin_client.post(f"/controle/registres/effacements/{a_verifier.id}", data={"decision": "ecarter"},
                          follow_redirects=True)
    assert "déjà été traitée" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Participant, intacte).nom == "ANONYME"
        ligne = db.session.get(EffacementRgpd, a_verifier.id)
        assert ligne.etat == "confirme" and ligne.decide_par_user_id is not None
    assert {e["etat"] for e in _entrees_du_fichier(donnees, intacte)} == {"confirme"}


def test_b_decision_ecarter_n_est_jamais_rejouee(app, donnees):
    from app.extensions import db
    from app.models import EffacementRgpd, Participant
    from app.services import registre_effacements as re_
    pid = _fiche(app)
    with app.app_context():
        p = db.session.get(Participant, pid)
        ancien = {"anonymises": [{"id": pid, "cree_le": p.created_at.isoformat(timespec="seconds")}]}
        nom = p.nom
    (donnees / "effacements.json").write_text(json.dumps(ancien), encoding="utf-8")
    with app.app_context():
        re_.reappliquer()
        ligne = EffacementRgpd.query.filter_by(participant_id=pid, etat="a_verifier").one()
        assert "écartée" in re_.decider(ligne.id, "ecarter")
        assert "déjà été traitée" in re_.decider(ligne.id, "confirmer")
        re_.reappliquer()
        assert db.session.get(Participant, pid).nom == nom


def test_b_ecritures_concurrentes_du_registre_des_effacements(donnees):
    from app.services import registre_effacements as re_
    depart = threading.Barrier(6)

    def ecrivain(i):
        depart.wait()
        for j in range(15):
            re_.fusionner_dans_fichier({f"k-{i}-{j}": {
                "nature": "anonymisation", "participant_id": i * 100 + j, "participant_cree_le": None,
                "precision": "microseconde", "etat": "confirme", "origine": "application",
                "cree_le": None, "decide_le": None, "note": None}})

    fils = [threading.Thread(target=ecrivain, args=(i,)) for i in range(6)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(60)
    assert len(re_.lire_fichier()) == 90


def test_b_registre_endommage_reconstitue_depuis_la_base(app, donnees):
    from app.extensions import db
    from app.models import Participant
    from app.services import registre_effacements as re_
    from app.services.purge_rgpd import anonymiser_participant
    from app.services.registre_externe import registres_mis_de_cote
    pid = _fiche(app)
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
    (donnees / "registre-effacements.json").write_text("{pas du json", encoding="utf-8")
    with app.app_context():
        entrees = re_.lire_fichier()
    assert any(e["participant_id"] == pid and e["etat"] == "confirme" for e in entrees.values())
    assert registres_mis_de_cote()


# ===========================================================================
# Restauration réelle d'un lot (parcours de l'application)
# ===========================================================================

@pytest.fixture()
def lots(app, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc
    dossier = tmp_path / "lots"
    dossier.mkdir()
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: dossier)
    with app.app_context():
        uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if not uri.startswith("sqlite") and not (svc._trouver_psql() and svc._trouver_pg_dump()):
            pytest.skip("psql ou pg_dump indisponible : restauration PostgreSQL non testable ici.")
    return dossier


def test_restauration_d_un_lot_ancien_reapplique_les_effacements(app, donnees, lots):
    from app.extensions import db
    from app.models import Participant
    from app.services import sauvegarde as svc
    from app.services.purge_rgpd import anonymiser_participant
    pid = _fiche(app)
    with app.app_context():
        nom = db.session.get(Participant, pid).nom
        lot = svc.creer_sauvegarde()["base"]                    # avant l'effacement
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        resultat = svc.restaurer_lot(lot)
        assert pid in resultat["reanonymises"] and resultat["erreur_rgpd"] is None
        assert db.session.get(Participant, pid).nom == "ANONYME" != nom


def test_restauration_refusee_si_un_effacement_n_est_pas_recopie(app, donnees, lots, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import registre_externe as reg
    from app.services import sauvegarde as svc
    from app.services.purge_rgpd import anonymiser_participant
    pid = _fiche(app)
    with app.app_context():
        lot = svc.creer_sauvegarde()["base"]
    ecrire = reg.ecrire
    monkeypatch.setattr(reg, "ecrire", lambda chemin, contenu: (_ for _ in ()).throw(
        reg.RegistreErreur("droits insuffisants", "test")))
    with app.app_context():
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        with pytest.raises(RuntimeError, match="effacements RGPD récents"):
            svc.restaurer_lot(lot)
        assert db.session.get(Participant, pid).nom == "ANONYME"   # base non écrasée
    monkeypatch.setattr(reg, "ecrire", ecrire)
    with app.app_context():
        resultat = svc.restaurer_lot(lot)                       # recopie faite d'abord
        assert pid in resultat["reanonymises"]


# ===========================================================================
# Sauvegarde, restauration, transfert et sinistre
# ===========================================================================

def test_lot_emporte_une_copie_des_registres_copiee_hors_serveur(app, donnees, lots, tmp_path):
    from app.extensions import db
    from app.models import Participant
    from app.services import sauvegarde as svc
    from app.services.financial_sequence import next_number
    from app.services.purge_rgpd import anonymiser_participant
    espace = _espace()
    pid = _fiche(app)
    with app.app_context():
        next_number(espace, [])
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        info = svc.creer_sauvegarde()
        copie = json.loads((lots / info["registres_fichier"]).read_text(encoding="utf-8"))
        assert copie["numeros"][espace] == 1
        assert any(e["participant_id"] == pid for e in copie["effacements"].values())
        controles = {c["nom"]: c["ok"] for c in svc.verifier_lot(info["base"])["controles"]}
        assert controles["Copie des registres"] is True
        dehors = tmp_path / "cle-usb"
        dehors.mkdir()
        [rapport] = svc.copier_lot_hors_serveur(info["base"], [dehors])
        assert rapport["ok"] and (dehors / info["registres_fichier"]).exists()


def test_restaurer_un_lot_ancien_ne_fait_rien_reculer(app, donnees, lots):
    from app.extensions import db
    from app.services import sauvegarde as svc
    from app.services.financial_sequence import lire_registre, next_number
    espace = _espace()
    with app.app_context():
        assert next_number(espace, []) == 1
        db.session.commit()
        lot = svc.creer_sauvegarde()["base"]
        assert next_number(espace, []) == 2          # émis après le lot
        assert next_number(espace, []) == 3
        db.session.commit()
        svc.restaurer_lot(lot)
        assert lire_registre()[espace] == 3
        assert next_number(espace, []) == 4          # jamais 2 ni 3 de nouveau
        db.session.commit()


def test_restaurer_sur_une_machine_neuve_rapporte_les_registres(app, donnees, lots, tmp_path, monkeypatch):
    from app.extensions import db
    from app.models import Participant
    from app.services import sauvegarde as svc
    from app.services.financial_sequence import lire_registre, next_number
    from app.services.purge_rgpd import anonymiser_participant
    espace = _espace()
    pid = _fiche(app)
    with app.app_context():
        for _ in range(5):
            next_number(espace, [])
        anonymiser_participant(db.session.get(Participant, pid))
        db.session.commit()
        lot = svc.creer_sauvegarde()["base"]
    neuve = tmp_path / "machine-neuve"
    (neuve / "runtime").mkdir(parents=True)
    monkeypatch.setenv("APP_DATA_DIR", str(neuve))              # registres vides
    from app.services.financial_sequence import RegistreBloque, blocage, retablir
    with app.app_context():
        svc.restaurer_lot(lot)
        # La base restaurée sait qu'un registre était tenu : sur cette machine
        # il manque, des numéros ont pu être émis après le lot → émission
        # suspendue, bornes connues = celles du lot (consolidation après #60).
        with pytest.raises(RegistreBloque):
            next_number(espace, [])
        db.session.rollback()
        bornes = blocage()["bornes"]
        assert bornes[espace] == 5
        retablir(dict(bornes))                                  # papier : rien de plus
        assert lire_registre()[espace] == 5
        assert next_number(espace, []) == 6
        db.session.commit()
    entrees = json.loads((neuve / "runtime/registre-effacements.json").read_text(encoding="utf-8"))["entrees"]
    assert any(e["participant_id"] == pid and e["etat"] == "confirme" for e in entrees.values())


def test_copie_des_registres_alteree_n_empeche_pas_la_restauration(app, donnees, lots):
    from app.services import sauvegarde as svc
    with app.app_context():
        info = svc.creer_sauvegarde()
        chemin = lots / info["registres_fichier"]
        copie = json.loads(chemin.read_text(encoding="utf-8"))
        copie["numeros"]["don:2099"] = 1                          # altération
        chemin.write_text(json.dumps(copie), encoding="utf-8")
        verification = svc.verifier_lot(info["base"])
        assert verification["ok"]
        assert {c["nom"]: c["ok"] for c in verification["controles"]}["Copie des registres"] is None
        resultat = svc.restaurer_lot(info["base"])
        assert "erreur" in resultat["registres"]
        from app.services.financial_sequence import lire_registre
        assert "don:2099" not in lire_registre()


def test_transfert_export_import_et_dernier_numero_declare(app, admin_client, donnees, tmp_path, monkeypatch):
    import io
    from app.extensions import db
    from app.services.financial_sequence import lire_registre, next_number
    with app.app_context():
        espace = f"don:{2090 + int(uuid.uuid4().int % 9)}"
        for _ in range(7):
            next_number(espace, [])
        db.session.commit()
    export = admin_client.get("/controle/registres/export")
    assert export.status_code == 200 and "attachment" in export.headers["Content-Disposition"]
    contenu = export.get_data()
    neuve = tmp_path / "nouvelle"
    (neuve / "runtime").mkdir(parents=True)
    monkeypatch.setenv("APP_DATA_DIR", str(neuve))
    # Copie retouchée : refusée.
    retouchee = json.loads(contenu)
    retouchee["numeros"][espace] = 1
    r = admin_client.post("/controle/registres/import", data={
        "fichier": (io.BytesIO(json.dumps(retouchee).encode()), "registres.json")},
        content_type="multipart/form-data", follow_redirects=True)
    assert "altérée" in r.get_data(as_text=True)
    admin_client.post("/controle/registres/import", data={"fichier": (io.BytesIO(contenu), "registres.json")},
                      content_type="multipart/form-data")
    from app.services.financial_sequence import blocage
    with app.app_context():
        # La base sait qu'un registre était tenu ; cette installation ne l'a
        # pas : émission suspendue, l'import relève les bornes connues.
        bornes = blocage()["bornes"]
        assert bornes[espace] >= 7
    # Le papier dit que le n° 12 a été émis : rétablissement explicite.
    serie, annee = espace.split(":")
    admin_client.post("/controle/registres/retablir",
                      data={**{f"max_{k}": str(v) for k, v in bornes.items()}, f"max_{espace}": "12"})
    admin_client.post("/controle/registres/dernier-numero", data={"serie": serie, "annee": annee, "numero": "3"})
    with app.app_context():
        assert lire_registre()[espace] == 12
        assert next_number(espace, []) == 13
        db.session.rollback()
    page = admin_client.get("/controle/registres").get_data(as_text=True)
    assert espace in page
