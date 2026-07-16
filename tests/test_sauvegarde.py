"""Tests de la sauvegarde intégrée : service réutilisable + page d'administration."""
import os
import time

import pytest


@pytest.fixture()
def backups_tmp(app, tmp_path, monkeypatch):
    """Isole les sauvegardes dans un dossier temporaire (Lot 2)."""
    from app.services import sauvegarde as svc
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: tmp_path)
    return tmp_path


def test_page_sauvegardes_accessible_admin(admin_client):
    r = admin_client.get("/admin/sauvegardes")
    assert r.status_code == 200
    assert "Sauvegarder maintenant" in r.get_data(as_text=True)


def test_page_sauvegardes_refusee_aux_anonymes(client):
    r = client.get("/admin/sauvegardes")
    assert r.status_code in (302, 401, 403)


def test_service_cree_et_liste_une_sauvegarde(app):
    from app.services.sauvegarde import (
        creer_sauvegarde,
        dossier_sauvegardes,
        lister_sauvegardes,
    )

    with app.app_context():
        info = creer_sauvegarde()
        out_dir = dossier_sauvegardes()
        db_path = out_dir / info["db_fichier"]
        zip_path = out_dir / info["uploads_fichier"]
        try:
            assert db_path.exists(), "le fichier de base doit être créé"
            assert zip_path.exists(), "l'archive des pièces jointes doit être créée"
            noms = {s["nom"] for s in lister_sauvegardes()}
            assert info["db_fichier"] in noms
            assert info["uploads_fichier"] in noms
        finally:
            db_path.unlink(missing_ok=True)
            zip_path.unlink(missing_ok=True)


def test_pg_dump_detecte_via_config(app, tmp_path, monkeypatch):
    """Si pg_dump n'est pas dans le PATH, la variable PG_DUMP_PATH est utilisée."""
    from app.services import sauvegarde

    monkeypatch.setattr(sauvegarde.shutil, "which", lambda *a, **k: None)
    faux = tmp_path / "pg_dump.exe"
    faux.write_text("")
    with app.app_context():
        app.config["PG_DUMP_PATH"] = str(faux)
        assert sauvegarde._trouver_pg_dump() == str(faux)


def test_bouton_cree_une_sauvegarde(admin_client, app):
    from app.services.sauvegarde import dossier_sauvegardes, lister_sauvegardes

    with app.app_context():
        avant = {s["nom"] for s in lister_sauvegardes()}

    r = admin_client.post("/admin/sauvegardes/creer", follow_redirects=True)
    assert r.status_code == 200
    assert "Sauvegarde créée" in r.get_data(as_text=True)

    with app.app_context():
        out_dir = dossier_sauvegardes()
        nouveaux = [s for s in lister_sauvegardes() if s["nom"] not in avant]
        try:
            assert nouveaux, "le bouton doit produire au moins un nouveau fichier"
        finally:
            for s in nouveaux:
                (out_dir / s["nom"]).unlink(missing_ok=True)


# --- Lot 2 : intégrité, rotation, alerte, restauration --------------------
def test_creation_et_integrite(app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        info = svc.creer_sauvegarde()
        base = info["base"]
        assert (backups_tmp / f"{base}.sha256").exists()
        assert svc.verifier_integrite(base) is True
        # corruption détectée
        (backups_tmp / info["db_fichier"]).write_bytes(b"corrompu")
        assert svc.verifier_integrite(base) is False


def test_derniere_et_jours(app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        assert svc.jours_depuis_derniere() is None
        svc.creer_sauvegarde()
        assert svc.jours_depuis_derniere() == 0
        assert svc.derniere_sauvegarde() is not None


def test_rotation(app, backups_tmp):
    from app.services import sauvegarde as svc

    for i in range(4):
        for suffixe in (".db", "_uploads.zip", ".sha256"):
            p = backups_tmp / f"lot{i}{suffixe}"
            p.write_text("x")
            t = time.time() + i  # lot3 = le plus récent
            os.utime(p, (t, t))

    with app.app_context():
        assert len(svc.lister_lots()) == 4
        assert svc.nettoyer_sauvegardes(max_lots=2) > 0
        restants = {l["base"] for l in svc.lister_lots()}
        assert restants == {"lot2", "lot3"}


def test_restauration_sqlite_self(app, backups_tmp):
    """Restaure l'état courant : données intactes + filet de sécurité créé."""
    from app.models import User
    from app.services import sauvegarde as svc

    with app.app_context():
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            pytest.skip("Restauration testée sur SQLite (environnement de test).")
        info = svc.creer_sauvegarde()
        avant = User.query.count()
        res = svc.restaurer_lot(info["base"])
        assert res["securite"]
        assert User.query.count() == avant  # base de nouveau interrogeable et intacte


def test_restauration_base_invalide(app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        with pytest.raises(RuntimeError):
            svc.restaurer_lot("inexistant_xyz")


def test_route_restaurer_base_vide(admin_client, backups_tmp):
    r = admin_client.post("/admin/sauvegardes/restaurer", data={"base": ""}, follow_redirects=True)
    assert r.status_code == 200
    assert "Sélectionnez une sauvegarde" in r.get_data(as_text=True)
