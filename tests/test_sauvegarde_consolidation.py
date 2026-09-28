"""Sauvegardes et installateur (audit 6.1 à 6.6, mineurs Windows) : ce qui se
teste hors Windows. Le reste est vérifié par la recette Windows de la CI
(desktop/SystemSmoke.cs : ports, sauvegarde quotidienne, restauration)."""
import os
import uuid
from pathlib import Path

import pytest


def _pg(app):
    return (app.config.get("SQLALCHEMY_DATABASE_URI") or "").startswith("postgresql")


# ---------------------------------------------------------------------------
# Lots non restaurables
# ---------------------------------------------------------------------------

def test_lot_tronque_jamais_restaure(app):
    """Sauvegarde coupée avant la fin (délai, pg_dump tué) : pas d'empreinte,
    fichier de base tronqué. La restauration refuse."""
    from app.services.sauvegarde import creer_sauvegarde, dossier_sauvegardes, restaurer_lot, verifier_lot
    with app.app_context():
        info = creer_sauvegarde()
        dossier = dossier_sauvegardes()
        (dossier / f"{info['base']}.sha256").unlink()
        base_fichier = dossier / info["db_fichier"]
        contenu = base_fichier.read_bytes()
        base_fichier.write_bytes(contenu[: len(contenu) // 3])
        assert verifier_lot(info["base"])["ok"] is False
        with pytest.raises(RuntimeError, match="non restaurable|corrompue"):
            restaurer_lot(info["base"])


def test_restauration_complete_a_blanc(app):
    """Le dernier lot se rejoue réellement dans une base vide jetable."""
    if not _pg(app):
        pytest.skip("restauration à blanc : PostgreSQL uniquement")
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from app.services.sauvegarde import creer_sauvegarde, restaurer_a_blanc
    with app.app_context():
        info = creer_sauvegarde()
        url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
        nom = f"mcs_essai_{uuid.uuid4().hex[:8]}"
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{nom}"'))
        try:
            rapport = restaurer_a_blanc(info["base"], url.set(database=nom).render_as_string(hide_password=False))
        finally:
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{nom}"'))
            admin.dispose()
        assert rapport["ok"] and rapport["tables"]["user"] >= 1
        assert rapport["revision"]
        assert rapport["documents"] >= 0


# ---------------------------------------------------------------------------
# Copies hors serveur réglées dans l'administration
# ---------------------------------------------------------------------------

def test_destinations_hors_serveur_reglees_dans_l_administration(app, admin_client, tmp_path):
    from app.services.sauvegarde import destinations_hors_serveur
    bonne = tmp_path / "nas"
    bonne.mkdir()
    absente = tmp_path / "disque-debranche" / "sauvegardes"
    r = admin_client.post("/admin/sauvegardes/hors-serveur",
                          data={"destinations": f"{bonne}\n{absente}"}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "écriture, relecture et effacement réussis" in page
    assert "injoignable" in page and "compte" in page
    with app.app_context():
        assert destinations_hors_serveur() == [bonne, absente]
    admin_client.post("/admin/sauvegardes/hors-serveur", data={"destinations": ""})
    with app.app_context():
        assert destinations_hors_serveur() == []


def test_etat_de_la_sauvegarde_quotidienne(app, admin_client):
    from app.services.sauvegarde import enregistrer_etat_sauvegarde, lire_etat_sauvegarde
    with app.app_context():
        etat = enregistrer_etat_sauvegarde("lot-test", True, {"ok": True, "controles": []},
                                           [{"destination": "D:\\copie", "ok": False, "detail": "disque plein"}])
        assert etat["ok"] is False and lire_etat_sauvegarde()["base"] == "lot-test"
    page = admin_client.get("/admin/sauvegardes").get_data(as_text=True)
    assert "Sauvegarde quotidienne" in page and "disque plein" in page


# ---------------------------------------------------------------------------
# Service Windows : parties testables hors Windows
# ---------------------------------------------------------------------------

def test_hotes_supplementaires_filtres():
    from desktop.runtime import hotes_supplementaires
    c = {"hotes_supplementaires": ["100.101.102.103", "Serveur.tail1.ts.net", "bad host!", "127.0.0.1"],
         "application_settings": {"ERP_LAN_HOSTS": "10.8.0.1, ,serveur.tail1.ts.net"}}
    assert hotes_supplementaires(c) == ["100.101.102.103", "serveur.tail1.ts.net", "10.8.0.1"]


def test_caddyfile_sans_http3_et_avec_adresses_vpn(tmp_path):
    from desktop.runtime import write_caddy
    texte = write_caddy({"hostname": "serveur", "https_port": 8443, "kiosk_http_port": 8080, "web_port": 8000,
                         "lan_ip": "192.168.1.20", "hotes_supplementaires": ["100.101.102.103"]},
                        tmp_path).read_text(encoding="utf-8")
    assert " servers {\n  protocols h1 h2\n }" in texte
    assert "https://100.101.102.103:8443" in texte and texte.count("https://192.168.1.20:8443") == 1


def test_delai_de_sauvegarde_proportionne(tmp_path, monkeypatch):
    from desktop import runtime
    monkeypatch.setattr(runtime, "taille_donnees", lambda root: 0)
    assert runtime.delai_sauvegarde(tmp_path) == 900
    monkeypatch.setattr(runtime, "taille_donnees", lambda root: 50 * 1024 ** 3)
    assert runtime.delai_sauvegarde(tmp_path) > 3600
    monkeypatch.setattr(runtime, "taille_donnees", lambda root: 10 ** 15)
    assert runtime.delai_sauvegarde(tmp_path) == 8 * 3600


def test_temoin_de_migration_longue(tmp_path):
    from desktop.runtime import temoin_frais
    temoin = tmp_path / "upgrading"
    assert not temoin_frais(temoin)
    temoin.write_text("1")
    assert temoin_frais(temoin)
    ancien = temoin.stat().st_mtime - 3600
    os.utime(temoin, (ancien, ancien))
    assert not temoin_frais(temoin)


def test_rotation_des_journaux(tmp_path):
    from desktop.runtime import tourner_journal
    journal = tmp_path / "runtime.log"
    journal.write_bytes(b"x" * 20)
    tourner_journal(journal, taille=10)
    assert not journal.exists() and (tmp_path / "runtime.previous.log").exists()


def test_arret_de_postgresql_detecte(tmp_path, monkeypatch):
    import subprocess
    from desktop import runtime
    monkeypatch.setattr(runtime, "postgres_bin", lambda root, c=None: tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 3))
    assert runtime.postgresql_en_marche({}, tmp_path) is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    assert runtime.postgresql_en_marche({}, tmp_path) is True


def test_rapport_de_reprise_hors_du_dossier_runtime():
    source = (Path(__file__).resolve().parents[1] / "desktop" / "migration.py").read_text(encoding="utf-8")
    assert 'root / "private" / "reprise-complete.json"' in source
    assert 'work / "complete.json"' not in source


def test_secrets_masques_dans_les_traces():
    source = (Path(__file__).resolve().parents[1] / "desktop" / "runtime.py").read_text(encoding="utf-8")
    assert '"token", "key"' in source


# ---------------------------------------------------------------------------
# 6.7 — autorité HTTPS contrainte
# ---------------------------------------------------------------------------

_CONFIG_PROXY = {"hostname": "serveur", "https_port": 8443, "kiosk_http_port": 8080, "web_port": 8000,
                 "lan_ip": "192.168.1.20", "hotes_supplementaires": ["serveur.tail1.ts.net", "203.0.113.7"]}


def test_installation_neuve_recoit_une_autorite_contrainte(tmp_path):
    import ipaddress
    from cryptography import x509
    from desktop.runtime import fichiers_autorite, write_caddy
    texte = write_caddy(dict(_CONFIG_PROXY), tmp_path).read_text(encoding="utf-8")
    certificat, cle = fichiers_autorite(tmp_path)
    assert " pki {\n  ca local {\n   root {" in texte and str(certificat).replace("\\", "/") in texte
    racine = x509.load_pem_x509_certificate(certificat.read_bytes())
    contraintes = racine.extensions.get_extension_for_class(x509.NameConstraints)
    assert contraintes.critical
    noms = {n.value for n in contraintes.value.permitted_subtrees if isinstance(n, x509.DNSName)}
    plages = {n.value for n in contraintes.value.permitted_subtrees if isinstance(n, x509.IPAddress)}
    assert {"serveur", "serveur.tail1.ts.net", "localhost"} <= noms
    assert ipaddress.ip_network("192.168.0.0/16") in plages and ipaddress.ip_network("203.0.113.7/32") in plages
    assert not any(p.prefixlen == 0 for p in plages), "aucune plage ne couvre tout l'Internet"
    # Réutilisée telle quelle : les postes qui lui font confiance restent valides.
    avant = certificat.read_bytes()
    write_caddy(dict(_CONFIG_PROXY), tmp_path)
    assert certificat.read_bytes() == avant and cle.exists()


def test_installation_existante_garde_son_autorite(tmp_path):
    from desktop.runtime import fichiers_autorite, write_caddy
    ancienne = tmp_path / "https" / "tls" / "pki" / "authorities" / "local" / "root.crt"
    ancienne.parent.mkdir(parents=True)
    ancienne.write_text("déjà déployée sur les postes")
    texte = write_caddy(dict(_CONFIG_PROXY), tmp_path).read_text(encoding="utf-8")
    assert " pki {" not in texte
    assert not fichiers_autorite(tmp_path)[0].exists()


def test_reecriture_des_chemins_par_lots(tmp_path):
    """Mineur de la reprise : 20 000 chemins réécrits en quelques secondes
    (une requête par chemin, sur colonne non indexée, était quadratique)."""
    import time
    from sqlalchemy import create_engine, text
    from app.services.instance_archive import remap_paths
    moteur = create_engine(f"sqlite:///{tmp_path / 'chemins.db'}")
    with moteur.begin() as c:
        c.execute(text("CREATE TABLE presence_activite (id INTEGER PRIMARY KEY, signature_path TEXT)"))
        c.execute(text("INSERT INTO presence_activite (id, signature_path) VALUES (:i, :p)"),
                  [{"i": i, "p": f"D:/ancien/uploads/signatures/sig_{i}.png"} for i in range(1, 20001)])
    debut = time.monotonic()
    with moteur.begin() as c:
        remap_paths(c, {"uploads": "D:/ancien/uploads"}, {"uploads": "/nouveau/uploads"})
    duree = time.monotonic() - debut
    with moteur.connect() as c:
        valeurs = [v for (v,) in c.execute(text("SELECT signature_path FROM presence_activite"))]
    assert all(v.replace("\\", "/").startswith("/nouveau/uploads/signatures/") for v in valeurs)
    assert duree < 30, duree


def _metadonnees_modele():
    import datetime as dt
    import sqlalchemy as sa
    meta = sa.MetaData()
    sa.Table("parent", meta, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("enfant", meta,
             sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("cree_le", sa.DateTime, nullable=False, default=lambda: dt.datetime(2024, 1, 1)),
             sa.Column("parent_id", sa.Integer, sa.ForeignKey("parent.id", ondelete="SET NULL"), nullable=True),
             sa.Column("quantite", sa.Integer, nullable=True))
    return meta


def _base_ancienne(url):
    import sqlalchemy as sa
    moteur = sa.create_engine(url)
    with moteur.begin() as c:
        c.execute(sa.text("DROP TABLE IF EXISTS enfant"))
        c.execute(sa.text("DROP TABLE IF EXISTS parent"))
        c.execute(sa.text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
        c.execute(sa.text("CREATE TABLE enfant (id INTEGER PRIMARY KEY, quantite VARCHAR(20))"))
        c.execute(sa.text("INSERT INTO enfant (id, quantite) VALUES (1, 'douze')"))
    return moteur


def test_reprise_colonne_obligatoire_a_defaut_calcule_et_ecart_de_type(tmp_path):
    import sqlalchemy as sa
    from desktop.migration import ecarts_de_type, reconcile_columns
    moteur = _base_ancienne(f"sqlite:///{tmp_path / 'ancienne.db'}")
    ajoutees = reconcile_columns(moteur, _metadonnees_modele())
    assert {"enfant.cree_le", "enfant.parent_id"} <= set(ajoutees)
    with moteur.connect() as c:
        assert c.execute(sa.text("SELECT cree_le FROM enfant")).scalar() is not None
    assert any(e.startswith("enfant.quantite") for e in ecarts_de_type(moteur, _metadonnees_modele()))


def test_reprise_cle_etrangere_recreee_sur_postgresql(app):
    import sqlalchemy as sa
    url = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if not url.startswith("postgresql"):
        pytest.skip("clé étrangère ajoutée : PostgreSQL uniquement")
    from desktop.migration import reconcile_columns
    from sqlalchemy.engine import make_url
    nom = f"mcs_reprise_fk_{uuid.uuid4().hex[:6]}"
    admin = sa.create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{nom}"'))
    try:
        moteur = _base_ancienne(make_url(url).set(database=nom).render_as_string(hide_password=False))
        reconcile_columns(moteur, _metadonnees_modele())
        cles = sa.inspect(moteur).get_foreign_keys("enfant")
        assert any(k["referred_table"] == "parent" and k["constrained_columns"] == ["parent_id"] for k in cles)
        moteur.dispose()
    finally:
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)'))
        admin.dispose()


def test_retour_arriere_des_migrations_de_la_consolidation(fresh_app):
    """Chaque migration de cette consolidation se défait proprement jusqu'à
    la révision de main (a4c7e2f9d153), puis se rejoue (procédure de retour
    arrière : voir la PR). Sur SQLite et PostgreSQL."""
    from flask_migrate import downgrade, upgrade
    from sqlalchemy import inspect, text
    from app.extensions import db
    with fresh_app.app_context():
        downgrade(revision="a4c7e2f9d153")
        with db.engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "a4c7e2f9d153"
        tables = set(inspect(db.engine).get_table_names())
        assert "encaissement" not in tables and "session_revoquee" not in tables
        upgrade(revision="head")
        with db.engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "b8d0f2a4c593"
        assert "encaissement" in set(inspect(db.engine).get_table_names())
