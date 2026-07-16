"""Étape 0 — Filet de sécurité : extraction zip durcie + vérification à blanc.

- une archive piégée (chemins « ../ » ou absolus) est refusée à la
  restauration, rien n'est écrit hors du dossier cible ;
- ``verifier_lot`` atteste qu'une vraie sauvegarde est restaurable et
  détecte une base corrompue ;
- le bouton « Vérifier » de la page admin fonctionne.
"""
import zipfile

import pytest


@pytest.fixture()
def backups_tmp(app, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc
    monkeypatch.setattr(svc, "dossier_sauvegardes", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Zip-slip
# ---------------------------------------------------------------------------

def _zip_piege_traversee(chemin):
    with zipfile.ZipFile(chemin, "w") as zf:
        zf.writestr("normal.txt", "ok")
        zf.writestr("../evasion.txt", "je sors du dossier")
    return chemin


def test_extraction_refuse_traversee(tmp_path):
    from app.services.sauvegarde import extraire_zip_securisee

    archive = _zip_piege_traversee(tmp_path / "piege.zip")
    dest = tmp_path / "dest"
    with pytest.raises(RuntimeError, match="refusée"):
        extraire_zip_securisee(archive, dest)
    # Rien ne doit avoir été écrit hors du dossier cible.
    assert not (tmp_path / "evasion.txt").exists()


def test_extraction_refuse_chemin_absolu(tmp_path):
    from app.services.sauvegarde import extraire_zip_securisee

    archive = tmp_path / "absolu.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/etc/evasion.txt", "chemin absolu")
    with pytest.raises(RuntimeError, match="refusée"):
        extraire_zip_securisee(archive, tmp_path / "dest")


def test_extraction_saine_fonctionne(tmp_path):
    from app.services.sauvegarde import extraire_zip_securisee

    archive = tmp_path / "sain.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("dossier/fichier.txt", "contenu")
        zf.writestr("racine.txt", "autre")
    dest = tmp_path / "dest"
    assert extraire_zip_securisee(archive, dest) == 2
    assert (dest / "dossier" / "fichier.txt").read_text() == "contenu"
    assert (dest / "racine.txt").read_text() == "autre"


# ---------------------------------------------------------------------------
# Vérification à blanc d'un lot
# ---------------------------------------------------------------------------

def test_verifier_lot_sauvegarde_reelle(app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        info = svc.creer_sauvegarde()
        rapport = svc.verifier_lot(info["base"])

    assert rapport["ok"] is True
    noms = {c["nom"]: c for c in rapport["controles"]}
    assert noms["Lot complet"]["ok"] is True
    assert noms["Empreintes sha256"]["ok"] is True
    assert noms["Pièces jointes"]["ok"] is True
    assert noms["Base de données"]["ok"] is True
    # La restauration à blanc doit avoir compté les tables témoins.
    assert "participant=" in noms["Base de données"]["detail"]


def test_verifier_lot_detecte_base_corrompue(app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        info = svc.creer_sauvegarde()
        (backups_tmp / info["db_fichier"]).write_bytes(b"pas une base sqlite")
        rapport = svc.verifier_lot(info["base"])

    assert rapport["ok"] is False
    noms = {c["nom"]: c for c in rapport["controles"]}
    assert noms["Empreintes sha256"]["ok"] is False
    assert noms["Base de données"]["ok"] is False


def test_verifier_lot_detecte_archive_piegee(app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        info = svc.creer_sauvegarde()
        _zip_piege_traversee(backups_tmp / info["uploads_fichier"])
        rapport = svc.verifier_lot(info["base"])

    assert rapport["ok"] is False
    noms = {c["nom"]: c for c in rapport["controles"]}
    assert noms["Pièces jointes"]["ok"] is False


def test_verifier_lot_dump_postgres_structurel(app, backups_tmp):
    from app.services.sauvegarde import _verifier_db_dump_postgres

    bon = backups_tmp / "bon.sql"
    tables = "".join(
        f"CREATE TABLE public.{t} (id integer);\nCOPY public.{t} (id) FROM stdin;\n\\.\n"
        for t in ("user", "participant", "atelier_activite", "session_activite", "subvention")
    ).replace("public.user ", 'public."user" ')
    bon.write_text(
        "-- PostgreSQL database dump\n" + tables + "-- PostgreSQL database dump complete\n",
        encoding="utf-8",
    )
    ok, detail = _verifier_db_dump_postgres(bon)
    assert ok, detail

    tronque = backups_tmp / "tronque.sql"
    tronque.write_text("-- PostgreSQL database dump\n" + tables, encoding="utf-8")
    ok, detail = _verifier_db_dump_postgres(tronque)
    assert not ok
    assert "tronqué" in detail


def test_restaurer_lot_refuse_archive_piegee(app, backups_tmp):
    """La restauration complète refuse une archive de pièces jointes piégée."""
    import time

    from app.services import sauvegarde as svc

    with app.app_context():
        if not app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
            pytest.skip("Restauration testée sur SQLite (environnement de test).")
        info = svc.creer_sauvegarde()
        # La sauvegarde de sécurité de restaurer_lot est nommée à la seconde :
        # on s'écarte d'une seconde pour qu'elle n'écrase pas le lot piégé.
        time.sleep(1.1)
        _zip_piege_traversee(backups_tmp / info["uploads_fichier"])
        # Ré-écrit l'empreinte pour passer le contrôle sha256 et atteindre l'extraction.
        sidecar = backups_tmp / f"{info['base']}.sha256"
        lignes = [
            f"{svc._sha256(backups_tmp / info['db_fichier'])}  {info['db_fichier']}",
            f"{svc._sha256(backups_tmp / info['uploads_fichier'])}  {info['uploads_fichier']}",
        ]
        sidecar.write_text("\n".join(lignes) + "\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="refusée"):
            svc.restaurer_lot(info["base"])


# ---------------------------------------------------------------------------
# Bouton « Vérifier » de la page admin
# ---------------------------------------------------------------------------

def test_bouton_verifier_page_admin(admin_client, app, backups_tmp):
    from app.services import sauvegarde as svc

    with app.app_context():
        info = svc.creer_sauvegarde()

    r = admin_client.post(
        "/admin/sauvegardes/verifier", data={"base": info["base"]}, follow_redirects=True
    )
    assert r.status_code == 200
    assert "RESTAURABLE" in r.get_data(as_text=True)


def test_bouton_verifier_base_vide(admin_client, backups_tmp):
    r = admin_client.post("/admin/sauvegardes/verifier", data={"base": ""}, follow_redirects=True)
    assert r.status_code == 200
    assert "Sélectionnez une sauvegarde" in r.get_data(as_text=True)
