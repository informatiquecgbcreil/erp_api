"""Sauvegardes 3-2-1 : 3 copies, sur 2 supports, dont 1 hors du centre.

Ce que ces tests garantissent :
- la copie cloud est chiffrée : aucun nom d'habitant n'y est lisible, et
  sans phrase secrète définie rien n'y part ;
- avec la phrase secrète, une copie cloud se rapatrie en sauvegarde
  ordinaire, restaurable ; une mauvaise phrase ou une copie abîmée ne
  laissent rien sur le serveur ;
- les trois voyants disent vrai, y compris « même disque que le serveur » ;
- conservation et seuil d'alerte se règlent dans l'administration.
"""
import uuid
from pathlib import Path

import pytest

PHRASE = "la tarte aux mirabelles du jeudi"

COLONNES = ("sauvegarde_hors_serveur", "sauvegarde_cloud", "sauvegarde_cle_publique", "sauvegarde_cle_privee",
            "sauvegarde_conservation_lots", "sauvegarde_alerte_jours")


@pytest.fixture()
def reglages(app, tmp_path, monkeypatch):
    """Réglages de sauvegarde à soi, remis comme avant ensuite ; dossier local isolé."""
    from app.extensions import db
    from app.models import InstanceSettings
    from app.services import sauvegarde as svc

    local = tmp_path / "backups"
    local.mkdir()
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: local)
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", "")
    with app.app_context():
        ligne = InstanceSettings.query.first()
        if ligne is None:
            ligne = InstanceSettings()
            db.session.add(ligne)
            db.session.commit()
        avant = {c: getattr(ligne, c) for c in COLONNES}
        for c in COLONNES:
            setattr(ligne, c, None)
        db.session.commit()

    def regler(**valeurs):
        with app.app_context():
            ligne = InstanceSettings.query.first()
            for cle, valeur in valeurs.items():
                setattr(ligne, cle, valeur)
            db.session.commit()

    regler.local = local
    yield regler
    regler(**avant)


def _avec_phrase(app, phrase=PHRASE):
    from app.extensions import db
    from app.services.sauvegarde import definir_phrase_secrete

    with app.app_context():
        definir_phrase_secrete(phrase)
        db.session.commit()


def _ecrire_lot(dossier: Path, base: str, contenu: bytes = b"donnees-de-base") -> str:
    from test_sauvegarde_hors_serveur import _ecrire_lot as ecrire

    return ecrire(dossier, base, contenu)


def test_copie_cloud_chiffree_sans_aucun_nom_lisible(app, reglages, tmp_path):
    from app.extensions import db
    from app.models import Participant
    from app.services.sauvegarde import copier_lot_hors_serveur, creer_sauvegarde

    nom = f"Zoubirou{uuid.uuid4().hex[:8]}"
    with app.app_context():
        db.session.add(Participant(nom=nom, prenom="Maelys"))
        db.session.commit()
    cloud = tmp_path / "OneDrive"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    _avec_phrase(app)

    with app.app_context():
        info = creer_sauvegarde()
        rapports = copier_lot_hors_serveur(info["base"])
    assert [r["ok"] for r in rapports] == [True], rapports
    assert rapports[0]["chiffre"] is True
    # Le lot du serveur contient bien le nom : le test n'est pas vide de sens.
    assert any(nom.encode() in p.read_bytes() for p in reglages.local.iterdir() if p.suffix in (".db", ".sql"))
    fichiers = sorted(p.name for p in cloud.iterdir())
    assert fichiers and all(n.endswith((".chiffre", ".chiffre.sha256")) for n in fichiers), fichiers
    for p in cloud.iterdir():
        assert nom.encode() not in p.read_bytes(), p.name


def test_sans_phrase_secrete_rien_ne_part_vers_le_cloud(app, reglages, tmp_path):
    from app.services.sauvegarde import copier_lot_hors_serveur, etat_321, etat_hors_serveur

    cloud = tmp_path / "Nextcloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    base = _ecrire_lot(reglages.local, "Centre_20261014_020000")
    with app.app_context():
        rapport, = copier_lot_hors_serveur(base)
        assert rapport["ok"] is False and "phrase secrète non définie" in rapport["detail"]
        assert list(cloud.iterdir()) == []
        assert etat_321(etat_hors_serveur())["hors_centre"]["ok"] is False


def test_rapatrier_avec_la_phrase_donne_un_lot_restaurable(app, reglages, tmp_path):
    from app.services.chiffrement_sauvegarde import PhraseIncorrecte
    from app.services.sauvegarde import (copier_lot_hors_serveur, lister_lots_cloud, rapatrier_lot_chiffre,
                                         verifier_integrite)

    cloud = tmp_path / "cloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    _avec_phrase(app)
    base = _ecrire_lot(reglages.local, "Centre_20261014_020000", b"contenu-a-retrouver")
    with app.app_context():
        assert copier_lot_hors_serveur(base)[0]["ok"]
    # Le serveur a tout perdu.
    for p in reglages.local.iterdir():
        p.unlink()

    with app.app_context():
        assert [lot["base"] for lot in lister_lots_cloud()] == [base]
        with pytest.raises(PhraseIncorrecte):
            rapatrier_lot_chiffre(base, "ce nest pas la bonne phrase")
        assert list(reglages.local.iterdir()) == []

        resultat = rapatrier_lot_chiffre(base, PHRASE)
        assert resultat["fichiers"] == 3
        assert verifier_integrite(base, reglages.local) is True
        assert (reglages.local / f"{base}.db").read_bytes() == b"contenu-a-retrouver"


def test_copie_cloud_abimee_signalee_et_refusee(app, reglages, tmp_path):
    from app.services.chiffrement_sauvegarde import CopieIllisible
    from app.services.sauvegarde import copier_lot_hors_serveur, etat_hors_serveur, rapatrier_lot_chiffre

    cloud = tmp_path / "cloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    _avec_phrase(app)
    base = _ecrire_lot(reglages.local, "Centre_20261014_020000")
    with app.app_context():
        assert copier_lot_hors_serveur(base)[0]["ok"]
    abime = cloud / f"{base}.db.chiffre"
    octets = bytearray(abime.read_bytes())
    octets[-20] ^= 1
    abime.write_bytes(bytes(octets))
    (reglages.local / f"{base}.db").unlink()

    with app.app_context():
        destination, = etat_hors_serveur()["destinations"]
        assert destination["alerte"] is True and "empreintes" in destination["detail"]
        with pytest.raises(CopieIllisible):
            rapatrier_lot_chiffre(base, PHRASE)
    assert not (reglages.local / f"{base}.db").exists()


def test_changer_de_phrase_garde_les_anciennes_copies_lisibles_avec_l_ancienne(app, reglages, tmp_path):
    from app.services.chiffrement_sauvegarde import PhraseIncorrecte
    from app.services.sauvegarde import copier_lot_hors_serveur, phrase_secrete_correcte, rapatrier_lot_chiffre

    cloud = tmp_path / "cloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    _avec_phrase(app)
    base = _ecrire_lot(reglages.local, "Centre_20261014_020000")
    with app.app_context():
        assert copier_lot_hors_serveur(base)[0]["ok"]
    _avec_phrase(app, "une nouvelle phrase pour la rentrée")
    (reglages.local / f"{base}.db").unlink()
    with app.app_context():
        assert phrase_secrete_correcte("une nouvelle phrase pour la rentrée")
        assert not phrase_secrete_correcte(PHRASE)
        with pytest.raises(PhraseIncorrecte):
            rapatrier_lot_chiffre(base, "une nouvelle phrase pour la rentrée")
        assert rapatrier_lot_chiffre(base, PHRASE)["fichiers"] == 3


def test_conservation_dans_le_cloud(app, reglages, tmp_path):
    from app.services.sauvegarde import copier_lot_hors_serveur, lister_lots_cloud

    cloud = tmp_path / "cloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud), sauvegarde_conservation_lots=2)
    _avec_phrase(app)
    with app.app_context():
        for jour in ("11", "12", "13"):
            base = _ecrire_lot(reglages.local, f"Centre_202610{jour}_020000")
            assert copier_lot_hors_serveur(base)[0]["ok"]
            # Des dates de fichiers distinctes, comme d'une nuit à l'autre.
            import os
            import time

            for p in cloud.glob(f"{base}*"):
                os.utime(p, (time.time() - (14 - int(jour)) * 86400,) * 2)
        assert [lot["base"] for lot in lister_lots_cloud()] == ["Centre_20261013_020000", "Centre_20261012_020000"]
    assert not list(cloud.glob("Centre_20261011*"))


def test_les_trois_voyants(app, reglages, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc

    base = _ecrire_lot(reglages.local, "Centre_20261014_020000")
    with app.app_context():
        voyants = svc.etat_321()
        assert not voyants["copies"]["ok"] and not voyants["supports"]["ok"] and not voyants["hors_centre"]["ok"]

    # Un « disque externe » qui est en fait un dossier du même disque.
    meme_disque = tmp_path / "Sauvegardes-USB"
    meme_disque.mkdir()
    reglages(sauvegarde_hors_serveur=str(meme_disque))
    with app.app_context():
        assert svc.copier_lot_hors_serveur(base)[0]["ok"]
        voyants = svc.etat_321()
        assert voyants["copies"]["ok"] and not voyants["supports"]["ok"]
        assert "disque du serveur" in voyants["supports"]["detail"]

    # Copie chiffrée dans le cloud, dont le dossier synchronisé est sur le
    # disque du serveur : elle vit chez l'hébergeur, c'est un autre support.
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    _avec_phrase(app)
    with app.app_context():
        assert all(r["ok"] for r in svc.copier_lot_hors_serveur(base))
        voyants = svc.etat_321()
        assert voyants["ok"] is True, voyants

    # Et un vrai autre disque compte aussi.
    reglages(sauvegarde_cloud=None)
    vrai = svc._peripherique
    monkeypatch.setattr(svc, "_peripherique", lambda p: -1 if "USB" in str(p) else vrai(p))
    with app.app_context():
        voyants = svc.etat_321()
        assert voyants["supports"]["ok"] and not voyants["hors_centre"]["ok"], voyants


def test_conservation_et_seuil_regles_dans_l_administration(app, reglages, monkeypatch):
    from app.services import sauvegarde as svc

    monkeypatch.setitem(app.config, "BACKUP_RETENTION_LOTS", 30)
    monkeypatch.setitem(app.config, "BACKUP_ALERT_DAYS", 2)
    with app.app_context():
        assert (svc.conservation_lots(), svc.seuil_alerte_jours()) == (30, 2)
    reglages(sauvegarde_conservation_lots=3, sauvegarde_alerte_jours=5)
    for jour in range(10, 15):
        _ecrire_lot(reglages.local, f"Centre_202610{jour}_020000")
    with app.app_context():
        assert (svc.conservation_lots(), svc.seuil_alerte_jours()) == (3, 5)
        svc.nettoyer_sauvegardes()
        assert len(svc.lister_lots()) == 3


@pytest.mark.parametrize("chemin, cloud", [
    (r"C:\Users\accueil\OneDrive - Centre\Sauvegardes", True),
    (r"G:\Mon Drive\sauvegardes", True),
    ("/home/accueil/Nextcloud/sauvegardes", True),
    (r"E:\Sauvegardes", False),
    (r"\\NAS\sauvegardes\centre", False),
])
def test_un_dossier_cloud_se_reconnait(chemin, cloud):
    from app.services.sauvegarde import ressemble_cloud

    assert ressemble_cloud(chemin) is cloud


def test_parcourir_ne_montre_que_des_dossiers(tmp_path):
    from app.services.parcourir_dossiers import racines, sous_dossiers

    (tmp_path / "Sauvegardes").mkdir()
    (tmp_path / "Archives").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "liste-des-adherents.xlsx").write_text("x")
    vue = sous_dossiers(str(tmp_path))
    assert vue["dossiers"] == ["Archives", "Sauvegardes"] and vue["erreur"] is None
    assert vue["parent"] == str(tmp_path.parent)
    assert sous_dossiers("relatif/chemin")["erreur"]
    assert sous_dossiers(str(tmp_path / "absent"))["erreur"]
    assert racines()


# ---------------------------------------------------------------------------
# L'écran : Administration › Sauvegardes
# ---------------------------------------------------------------------------

def _texte(reponse):
    import html

    return html.unescape(reponse.get_data(as_text=True))


def test_la_page_montre_les_trois_cases_et_les_voyants(admin_client, reglages, client):
    page = _texte(admin_client.get("/admin/sauvegardes"))
    for morceau in ("La règle 3-2-1", "① Sur ce serveur", "② Autre support dans le centre",
                    "③ Hors du centre", "Aucune copie hors serveur configurée", "Phrase secrète", "Parcourir"):
        assert morceau in page, morceau
    assert client.get("/admin/sauvegardes").status_code in (302, 401, 403)


def test_phrase_secrete_controlee_puis_jamais_enregistree(app, admin_client, reglages):
    from app.extensions import db
    from app.models import AuditLog, InstanceSettings
    from app.services.sauvegarde import phrase_secrete_correcte, trousseau

    def envoyer(**champs):
        return _texte(admin_client.post("/admin/sauvegardes/phrase", data=champs, follow_redirects=True))

    assert "au moins 16 caractères" in envoyer(phrase="trop courte", confirmation="trop courte", notee="1")
    assert "pas identiques" in envoyer(phrase=PHRASE, confirmation=PHRASE + "!", notee="1")
    assert "notée hors du serveur" in envoyer(phrase=PHRASE, confirmation=PHRASE)
    with app.app_context():
        assert trousseau() is None

    assert "Phrase secrète enregistrée" in envoyer(phrase=PHRASE, confirmation=PHRASE, notee="1")
    with app.app_context():
        assert phrase_secrete_correcte(PHRASE)
        ligne = InstanceSettings.query.first()
        enregistre = " ".join(str(getattr(ligne, c.name)) for c in InstanceSettings.__table__.columns)
        enregistre += " ".join(str(j.details) for j in db.session.query(AuditLog).all())
        assert PHRASE not in enregistre

    nouvelle = "une autre phrase toute neuve"
    assert "avec l'ancienne phrase" in envoyer(phrase=nouvelle, confirmation=nouvelle, notee="1")
    ok = _texte(admin_client.post("/admin/sauvegardes/phrase/verifier", data={"phrase": nouvelle}, follow_redirects=True))
    assert "bonne phrase secrète" in ok
    ko = _texte(admin_client.post("/admin/sauvegardes/phrase/verifier", data={"phrase": PHRASE}, follow_redirects=True))
    assert "pas la phrase secrète actuelle" in ko


def test_dossier_cloud_teste_et_rappel_de_la_phrase(app, admin_client, reglages, tmp_path):
    from app.services.sauvegarde import destination_cloud

    cloud = tmp_path / "Sauvegardes-cloud"
    cloud.mkdir()
    page = _texte(admin_client.post("/admin/sauvegardes/cloud", data={"dossier": str(cloud)}, follow_redirects=True))
    assert "écriture, relecture et effacement réussis" in page
    assert "Définissez maintenant la phrase secrète" in page
    with app.app_context():
        assert destination_cloud() == cloud

    # Le même dossier ne peut pas servir de copie en clair et de copie chiffrée.
    support = tmp_path / "USB"
    support.mkdir()
    reglages(sauvegarde_hors_serveur=str(support))
    page = _texte(admin_client.post("/admin/sauvegardes/cloud", data={"dossier": str(support)}, follow_redirects=True))
    assert "déjà une copie en clair" in page
    with app.app_context():
        assert destination_cloud() == cloud


def test_autre_support_avertit_cloud_et_meme_disque(app, admin_client, reglages, tmp_path):
    from app.services.sauvegarde import destinations_support

    onedrive = tmp_path / "OneDrive - Centre"
    onedrive.mkdir()
    local = tmp_path / "copie-locale"
    local.mkdir()
    page = _texte(admin_client.post("/admin/sauvegardes/hors-serveur",
                                    data={"destinations": [str(onedrive), str(local), ""]}, follow_redirects=True))
    assert "ressemble à un dossier synchronisé vers un cloud" in page
    assert "même disque que le serveur" in page
    with app.app_context():
        assert destinations_support() == [onedrive, local]


def test_reglages_de_conservation(app, admin_client, reglages):
    from app.services.sauvegarde import conservation_lots, seuil_alerte_jours

    page = _texte(admin_client.post("/admin/sauvegardes/reglages", data={"conservation": "1", "seuil": "2"},
                                    follow_redirects=True))
    assert "entre 3 et 365" in page
    page = _texte(admin_client.post("/admin/sauvegardes/reglages", data={"conservation": "14", "seuil": "3"},
                                    follow_redirects=True))
    assert "14 sauvegardes gardées" in page
    with app.app_context():
        assert (conservation_lots(), seuil_alerte_jours()) == (14, 3)


def test_parcourir_les_dossiers_du_serveur(admin_client, client, tmp_path):
    (tmp_path / "Sauvegardes").mkdir()
    vue = admin_client.get("/admin/sauvegardes/parcourir", query_string={"chemin": str(tmp_path)}).get_json()
    assert "Sauvegardes" in vue["dossiers"] and vue["racines"]
    assert admin_client.get("/admin/sauvegardes/parcourir").get_json()["racines"]
    assert client.get("/admin/sauvegardes/parcourir").status_code in (302, 401, 403)


def test_rapatrier_depuis_l_ecran(app, admin_client, reglages, tmp_path):
    from app.services.sauvegarde import copier_lot_hors_serveur

    cloud = tmp_path / "cloud"
    cloud.mkdir()
    reglages(sauvegarde_cloud=str(cloud))
    _avec_phrase(app)
    base = _ecrire_lot(reglages.local, "Centre_20261014_020000")
    with app.app_context():
        assert copier_lot_hors_serveur(base)[0]["ok"]
    for p in reglages.local.iterdir():
        p.unlink()

    page = _texte(admin_client.get("/admin/sauvegardes"))
    assert "Copies dans le cloud" in page and "Rapatrier" in page
    ko = _texte(admin_client.post("/admin/sauvegardes/cloud/rapatrier", data={"base": base, "phrase": "mauvaise phrase ici"},
                                  follow_redirects=True))
    assert "Phrase secrète incorrecte" in ko and not list(reglages.local.iterdir())
    ok = _texte(admin_client.post("/admin/sauvegardes/cloud/rapatrier", data={"base": base, "phrase": PHRASE},
                                  follow_redirects=True))
    assert "rapatriée et vérifiée" in ok
    assert (reglages.local / f"{base}.db").exists()


def test_le_rapport_sos_dit_l_etat_3_2_1_sans_chemin(app, admin_client, reglages, tmp_path):
    secret = tmp_path / "OneDrive - Jean Dupont"
    secret.mkdir()
    reglages(sauvegarde_cloud=str(secret))
    from test_rapport_sos import _rapport_affiche

    rapport = _rapport_affiche(admin_client.post("/admin/sante/sos", data={"action": "afficher"}))
    assert "Règle 3-2-1" in rapport and "1 hors du centre NON" in rapport and "phrase secrète non définie" in rapport
    assert "Dupont" not in rapport
