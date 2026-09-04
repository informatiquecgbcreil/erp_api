"""Copies hors serveur des sauvegardes.

Ce que ces tests protègent : une sauvegarde rangée sur la machine qu'elle
protège disparaît avec elle. La chaîne de survie ne tient que si la copie
externe part vraiment, arrive intacte, et se plaint quand elle n'arrive pas.
"""
import os
import time
from pathlib import Path

import pytest


ZIP_VIDE = b"PK\x05\x06" + b"\x00" * 18


@pytest.fixture()
def backups_tmp(app, tmp_path, monkeypatch):
    """Isole le dossier de sauvegardes local dans un dossier temporaire."""
    from app.services import sauvegarde as svc

    local = tmp_path / "backups"
    local.mkdir()
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: local)
    return local


def _ecrire_lot(dossier: Path, base: str, contenu: bytes = b"donnees-de-base") -> str:
    """Fabrique un lot complet et cohérent (base + uploads + empreintes)."""
    from app.services.sauvegarde import _sha256

    db = dossier / f"{base}.db"
    uploads = dossier / f"{base}_uploads.zip"
    db.write_bytes(contenu)
    uploads.write_bytes(ZIP_VIDE)
    (dossier / f"{base}.sha256").write_text(
        f"{_sha256(db)}  {db.name}\n{_sha256(uploads)}  {uploads.name}\n",
        encoding="utf-8",
    )
    return base


def _vieillir(dossier: Path, jours: int) -> None:
    ancien = time.time() - jours * 86400
    for p in dossier.iterdir():
        os.utime(p, (ancien, ancien))


# --------------------------------------------------------------------------
# Configuration des destinations
# --------------------------------------------------------------------------
def test_destinations_separees_par_points_virgules_et_lignes(app, monkeypatch):
    """Le séparateur est le point-virgule : les chemins Windows contiennent
    des deux-points (``D:\\...``) et parfois des virgules."""
    from app.services.sauvegarde import destinations_hors_serveur

    monkeypatch.setitem(
        app.config,
        "BACKUP_OFFSITE_DIRS",
        ' D:\\Sauvegardes ;; "\\\\NAS\\backups"\n/mnt/disque ; ',
    )
    with app.app_context():
        chemins = [str(p) for p in destinations_hors_serveur()]
    assert chemins == ["D:\\Sauvegardes", "\\\\NAS\\backups", "/mnt/disque"]


def test_aucune_destination_configuree_vaut_alerte(app, monkeypatch):
    """Pas de copie externe = le pire des cas, donc une alerte — jamais un silence."""
    from app.services.sauvegarde import copier_lot_hors_serveur, etat_hors_serveur

    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", "")
    with app.app_context():
        etat = etat_hors_serveur()
        assert etat["configure"] is False
        assert etat["alerte"] is True
        assert copier_lot_hors_serveur() == []


# --------------------------------------------------------------------------
# Copie effective et vérifiée
# --------------------------------------------------------------------------
def test_copie_arrive_complete_et_verifiee(app, backups_tmp, tmp_path, monkeypatch):
    from app.services.sauvegarde import (
        copier_lot_hors_serveur,
        etat_hors_serveur,
        verifier_integrite,
    )

    base = _ecrire_lot(backups_tmp, "Structure_20260101_020000")
    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    with app.app_context():
        rapports = copier_lot_hors_serveur(base)
        assert len(rapports) == 1
        assert rapports[0]["ok"] is True, rapports[0]["detail"]
        assert rapports[0]["fichiers"] == 3

        assert (dest / f"{base}.db").exists()
        assert (dest / f"{base}_uploads.zip").exists()
        assert (dest / f"{base}.sha256").exists()
        assert verifier_integrite(base, dest) is True

        etat = etat_hors_serveur()
        assert etat["alerte"] is False
        assert etat["destinations"][0]["dernier_lot"] == base


def test_lot_par_defaut_est_le_plus_recent(app, backups_tmp, tmp_path, monkeypatch):
    from app.services.sauvegarde import copier_lot_hors_serveur

    _ecrire_lot(backups_tmp, "Structure_20260101_020000")
    _vieillir(backups_tmp, 5)
    recent = _ecrire_lot(backups_tmp, "Structure_20260210_020000")
    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    with app.app_context():
        assert copier_lot_hors_serveur()[0]["ok"] is True

    assert (dest / f"{recent}.db").exists()
    assert not (dest / "Structure_20260101_020000.db").exists()


def test_copie_alteree_detectee_et_rien_de_partiel_laisse(app, backups_tmp, tmp_path, monkeypatch):
    """Disque plein, lien réseau coupé : la copie tronquée doit être vue tout de
    suite, et ne jamais laisser un lot d'apparence complète à la destination."""
    from app.services import sauvegarde as svc

    base = _ecrire_lot(backups_tmp, "Structure_20260101_020000")
    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    def copie_tronquee(src, dst, *a, **k):
        Path(dst).write_bytes(b"tronque")
        return dst

    monkeypatch.setattr(svc.shutil, "copy2", copie_tronquee)

    with app.app_context():
        rapports = svc.copier_lot_hors_serveur(base)

    assert rapports[0]["ok"] is False
    assert "altérée" in rapports[0]["detail"]
    assert not (dest / f"{base}.db").exists(), "aucun fichier définitif ne doit rester"
    assert list(dest.glob("*.part")) == [], "le fichier provisoire doit être nettoyé"


def test_destination_injoignable_nempeche_pas_les_autres(app, backups_tmp, tmp_path, monkeypatch):
    """Un NAS débranché ne doit pas priver de copie le disque externe qui, lui,
    est bien là — et il ne doit pas être silencieusement recréé en local."""
    from app.services.sauvegarde import copier_lot_hors_serveur

    base = _ecrire_lot(backups_tmp, "Structure_20260101_020000")
    absente = tmp_path / "montage-absent" / "appgestion"
    bonne = tmp_path / "disque-externe"
    bonne.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", f"{absente};{bonne}")

    with app.app_context():
        rapports = copier_lot_hors_serveur(base)

    assert [r["ok"] for r in rapports] == [False, True]
    assert "injoignable" in rapports[0]["detail"]
    assert not absente.exists(), "un parent absent ne doit jamais être fabriqué"
    assert (bonne / f"{base}.db").exists()


def test_destination_creee_si_son_parent_existe(app, backups_tmp, tmp_path, monkeypatch):
    from app.services.sauvegarde import copier_lot_hors_serveur

    base = _ecrire_lot(backups_tmp, "Structure_20260101_020000")
    dest = tmp_path / "appgestion"  # parent (tmp_path) présent
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    with app.app_context():
        assert copier_lot_hors_serveur(base)[0]["ok"] is True
    assert (dest / f"{base}.db").exists()


# --------------------------------------------------------------------------
# Rotation et alertes
# --------------------------------------------------------------------------
def test_rotation_appliquee_a_la_destination(app, backups_tmp, tmp_path, monkeypatch):
    """La destination ne doit pas saturer : elle garde le même nombre de lots."""
    from app.services.sauvegarde import copier_lot_hors_serveur

    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_RETENTION_LOTS", 2)

    with app.app_context():
        for jour in ("01", "02", "03", "04"):
            base = _ecrire_lot(backups_tmp, f"Structure_202601{jour}_020000")
            copier_lot_hors_serveur(base)
            _vieillir(backups_tmp, 0)

    restants = sorted(p.name for p in dest.glob("*.db"))
    assert restants == [
        "Structure_20260103_020000.db",
        "Structure_20260104_020000.db",
    ], restants


def test_copie_trop_ancienne_signalee(app, backups_tmp, tmp_path, monkeypatch):
    """La tâche planifiée a cessé de tourner : l'écran d'administration doit le dire."""
    from app.services.sauvegarde import copier_lot_hors_serveur, etat_hors_serveur

    base = _ecrire_lot(backups_tmp, "Structure_20260101_020000")
    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_ALERT_DAYS", 2)

    with app.app_context():
        copier_lot_hors_serveur(base)
        _vieillir(dest, 9)
        etat = etat_hors_serveur()

    assert etat["alerte"] is True
    destination = etat["destinations"][0]
    assert destination["jours_depuis"] >= 9
    assert "9 jour" in destination["detail"]


def test_destination_vide_signalee(app, tmp_path, monkeypatch):
    from app.services.sauvegarde import etat_hors_serveur

    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    with app.app_context():
        etat = etat_hors_serveur()

    assert etat["alerte"] is True
    assert "aucun lot complet" in etat["destinations"][0]["detail"]


def test_digest_signale_absence_de_copie_hors_serveur(app, monkeypatch):
    """Le rappel automatique doit nommer le risque, pas seulement l'ancienneté."""
    from app.services.notifications import _lignes_sauvegarde
    from datetime import date

    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", "")
    with app.app_context():
        lignes = _lignes_sauvegarde(2, date.today())

    assert any("hors serveur" in ligne for ligne in lignes), lignes


def test_digest_signale_destination_injoignable(app, tmp_path, monkeypatch):
    from app.services.notifications import _lignes_sauvegarde
    from datetime import date

    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(tmp_path / "jamais-monte"))
    with app.app_context():
        lignes = _lignes_sauvegarde(2, date.today())

    assert any("injoignable" in ligne for ligne in lignes), lignes


# --------------------------------------------------------------------------
# Bout en bout : vraie sauvegarde + page d'administration
# --------------------------------------------------------------------------
def test_sauvegarde_reelle_copiee_hors_serveur(app, backups_tmp, tmp_path, monkeypatch):
    from app.services.sauvegarde import (
        copier_lot_hors_serveur,
        creer_sauvegarde,
        verifier_integrite,
    )

    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    with app.app_context():
        info = creer_sauvegarde()
        rapports = copier_lot_hors_serveur(info["base"])
        assert rapports[0]["ok"] is True, rapports[0]["detail"]
        assert verifier_integrite(info["base"], dest) is True

    assert (dest / info["db_fichier"]).exists()
    assert (dest / info["uploads_fichier"]).exists()


def test_bouton_sauvegarder_declenche_la_copie(admin_client, app, backups_tmp, tmp_path, monkeypatch):
    dest = tmp_path / "nas"
    dest.mkdir()
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", str(dest))

    r = admin_client.post("/admin/sauvegardes/creer", follow_redirects=True)
    assert r.status_code == 200
    assert "Copiée hors serveur" in r.get_data(as_text=True)
    assert list(dest.glob("*_uploads.zip")), "le lot doit être arrivé à destination"


def test_page_admin_alerte_si_aucune_destination(admin_client, app, monkeypatch):
    monkeypatch.setitem(app.config, "BACKUP_OFFSITE_DIRS", "")
    r = admin_client.get("/admin/sauvegardes")
    assert r.status_code == 200
    assert "Aucune copie hors serveur configurée" in r.get_data(as_text=True)
