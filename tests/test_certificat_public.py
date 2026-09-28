"""Certificat reconnu (Let's Encrypt, défi DNS par cPanel).

Bout en bout contre Pebble (serveur ACME de test de Let's Encrypt) et
pebble-challtestsrv (serveur DNS de test) ; cPanel est simulé en HTTPS avec
l'API 2 ZoneEdit (mêmes appels qu'acme.sh dns_cpanel). Sans Pebble installé
(`go install github.com/letsencrypt/pebble/v2/cmd/...@latest`), ces tests
sont ignorés ; les autres tournent partout.
"""
import datetime
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from desktop import certificat_public as cp

RACINE_DEPOT = Path(__file__).resolve().parents[1]
from desktop.recette_certificat import JETON, NOM, UTILISATEUR, FauxCPanel, binaire  # noqa: E402
from desktop import recette_certificat as banc  # noqa: E402

avec_pebble = pytest.mark.skipif(not (binaire("pebble") and binaire("pebble-challtestsrv")), reason="Pebble non installé")


@pytest.fixture
def pebble(tmp_path):
    try:
        with banc.pebble(tmp_path / "pebble") as info:
            yield info
    except FileNotFoundError:
        pytest.skip("Pebble non installé")


def _config(root, cpanel_url, cpanel_ca, pebble=None, jeton=JETON):
    reglages = {"nom": NOM, "cpanel_hote": cpanel_url, "cpanel_utilisateur": UTILISATEUR, "cpanel_ca": str(cpanel_ca)}
    if pebble:
        reglages.update(acme_repertoire=pebble["repertoire"], acme_ca=pebble["ca"])
    return {"data_root": str(root), "hostname": "serveur", "https_port": 8443, "kiosk_http_port": 8080,
            "web_port": 18080, "lan_ip": "192.168.1.200", "network": True, "url": "https://serveur:8443",
            "certificat_public": reglages, "certificat_public_jeton": jeton}


def _racines_pebble(pebble):
    return banc.racines_pebble(pebble)


def _chaine_valide(chaine_pem, racines, nom):
    from cryptography.x509.verification import PolicyBuilder, Store
    certs = x509.load_pem_x509_certificates(chaine_pem)
    PolicyBuilder().store(Store(racines)).build_server_verifier(x509.DNSName(nom)).verify(certs[0], certs[1:])
    return True


# ---------------------------------------------------------------------------
# Bout en bout
# ---------------------------------------------------------------------------

@avec_pebble
def test_certificat_obtenu_par_le_dns_cpanel_et_reconnu(tmp_path, pebble):
    cpanel = FauxCPanel(tmp_path / "cpanel", pebble["gestion_dns"], retard=1.5)
    try:
        c = _config(tmp_path / "donnees", cpanel.url, cpanel.ca, pebble)
        code = cp.executer(c, serveurs_dns=[pebble["dns"]], delai_publication=30)
        assert code == cp.NOUVEAU, pebble["journal"].read_text(errors="replace")
        certificat, cle = cp.fichiers(tmp_path / "donnees")
        assert _chaine_valide(certificat.read_bytes(), _racines_pebble(pebble), NOM)
        assert cp.certificat_valide(c, tmp_path / "donnees") is not None
        # Enregistrement de preuve retiré de la zone.
        assert cpanel.enregistrements == []
        assert "add_zone_record" in cpanel.appels and "remove_zone_record" in cpanel.appels
        # Clé du compte dans le dossier réservé ; état sans secret pour le web.
        assert (tmp_path / "donnees" / "private" / "acme" / "compte.pem").exists()
        etat = cp.fichier_etat(tmp_path / "donnees").read_text(encoding="utf-8")
        assert NOM in etat and JETON not in etat and '"erreur": null' in etat
        # Valable et loin de l'échéance : rien ne se passe.
        appels = len(cpanel.appels)
        assert cp.executer(c, serveurs_dns=[pebble["dns"]]) == 0
        assert len(cpanel.appels) == appels
        # Renouvellement forcé : même compte, nouveau certificat et nouvelle clé.
        avant = cle.read_bytes()
        assert cp.executer(c, forcer=True, serveurs_dns=[pebble["dns"]], delai_publication=30) == cp.NOUVEAU
        assert cle.read_bytes() != avant and cp.certificat_valide(c, tmp_path / "donnees") is not None
    finally:
        cpanel.fermer()


@avec_pebble
def test_mode_ligne_de_commande_et_caddyfile(tmp_path, pebble):
    """Chemin réel : runtime.py --certificat-public (code 10), puis le
    Caddyfile sert le nom avec ce certificat."""
    cpanel = FauxCPanel(tmp_path / "cpanel", pebble["gestion_dns"])
    try:
        c = _config(tmp_path / "donnees", cpanel.url, cpanel.ca, pebble)
        # Le mode ligne de commande cherche les serveurs de la zone : on les
        # impose ici (sinon interrogation de résolveurs publics).
        c["certificat_public"]["dns_serveurs"] = [list(pebble["dns"])]
        fini = subprocess.run([sys.executable, "-B", str(RACINE_DEPOT / "desktop" / "runtime.py"), "--certificat-public"],
                              input=json.dumps(c).encode(), capture_output=True, cwd=RACINE_DEPOT, timeout=180)
        assert fini.returncode == 10, fini.stdout.decode(errors="replace") + fini.stderr.decode(errors="replace")
        from desktop.runtime import write_caddy
        texte = write_caddy(c, tmp_path / "donnees").read_text(encoding="utf-8")
        certificat, cle = cp.fichiers(tmp_path / "donnees")
        assert f"https://{NOM}:8443 {{\n tls {json.dumps(str(certificat))} {json.dumps(str(cle))}\n" in texte
        interne = texte.split(" tls internal")[0].rsplit("\n", 2)[-2]
        assert NOM not in interne, "le nom public ne doit pas passer par l'autorité du centre"
        fini = subprocess.run([sys.executable, "-B", str(RACINE_DEPOT / "desktop" / "runtime.py"), "--certificat-public"],
                              input=json.dumps(c).encode(), capture_output=True, cwd=RACINE_DEPOT, timeout=180)
        assert fini.returncode == 0
    finally:
        cpanel.fermer()


@avec_pebble
def test_jeton_refuse_rien_ne_change(tmp_path, pebble):
    cpanel = FauxCPanel(tmp_path / "cpanel", pebble["gestion_dns"])
    try:
        c = _config(tmp_path / "donnees", cpanel.url, cpanel.ca, pebble, jeton="mauvais")
        with pytest.raises(cp.ErreurCertificat, match="refuse l'identifiant ou le jeton"):
            cp.executer(c, serveurs_dns=[pebble["dns"]])
        assert not cp.fichiers(tmp_path / "donnees")[0].exists()
        etat = json.loads(cp.fichier_etat(tmp_path / "donnees").read_text(encoding="utf-8"))
        assert "jeton" in etat["erreur"] and "mauvais" not in json.dumps(etat)
        fini = subprocess.run([sys.executable, "-B", str(RACINE_DEPOT / "desktop" / "runtime.py"), "--certificat-public"],
                              input=json.dumps(c).encode(), capture_output=True, cwd=RACINE_DEPOT, timeout=60)
        assert fini.returncode == 1 and "jeton" in fini.stdout.decode()
    finally:
        cpanel.fermer()


@avec_pebble
def test_publication_dns_trop_lente_certificat_precedent_garde(tmp_path, pebble):
    cpanel = FauxCPanel(tmp_path / "cpanel", pebble["gestion_dns"])
    try:
        c = _config(tmp_path / "donnees", cpanel.url, cpanel.ca, pebble)
        assert cp.executer(c, serveurs_dns=[pebble["dns"]], delai_publication=30) == cp.NOUVEAU
        certificat = cp.fichiers(tmp_path / "donnees")[0]
        avant = certificat.read_bytes()
        cpanel.gestion_dns = None  # plus rien n'est publié
        # Nouveau compte : Let's Encrypt (et Pebble) réutilisent sinon
        # l'autorisation déjà validée, sans nouveau défi DNS.
        for fichier in ("compte.pem", "comptes.json"):
            (tmp_path / "donnees" / "private" / "acme" / fichier).unlink()
        with pytest.raises(cp.ErreurCertificat, match="pas publié"):
            cp.executer(c, forcer=True, serveurs_dns=[pebble["dns"]], delai_publication=2)
        assert certificat.read_bytes() == avant and cp.certificat_valide(c, tmp_path / "donnees") is not None
        assert cpanel.enregistrements == [], "preuve retirée même en cas d'échec"
    finally:
        cpanel.fermer()


@avec_pebble
def test_nom_hors_des_zones_du_cpanel(tmp_path, pebble):
    cpanel = FauxCPanel(tmp_path / "cpanel", pebble["gestion_dns"], zones=("autre-domaine.test",))
    try:
        c = _config(tmp_path / "donnees", cpanel.url, cpanel.ca, pebble)
        with pytest.raises(cp.ErreurCertificat, match="Aucune zone DNS"):
            cp.executer(c, serveurs_dns=[pebble["dns"]])
    finally:
        cpanel.fermer()


@avec_pebble
def test_requete_dns_directe(pebble):
    urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{pebble['gestion_dns']}/set-txt",
        data=json.dumps({"host": "_acme-challenge.essai.test.", "value": "valeur-attendue"}).encode(), method="POST"), timeout=5)
    hote, port = pebble["dns"]
    assert "valeur-attendue" in cp.requete_dns("_acme-challenge.essai.test", "TXT", hote, port, recursion=False)
    assert cp.attendre_publication("_acme-challenge.essai.test", "valeur-attendue", [pebble["dns"]], delai=5)
    assert not cp.attendre_publication("_acme-challenge.essai.test", "autre", [pebble["dns"]], delai=1, pause=0.2)


# ---------------------------------------------------------------------------
# Sans Pebble
# ---------------------------------------------------------------------------

def _certificat_signe(root, nom, *, jours=90, debut_il_y_a=0, cle_differente=False):
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    cle = ec.generate_private_key(ec.SECP256R1())
    autre = ec.generate_private_key(ec.SECP256R1())
    debut = maintenant - datetime.timedelta(days=debut_il_y_a, minutes=5)
    feuille = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nom)]))
               .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")]))
               .public_key(cle.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(debut).not_valid_after(debut + datetime.timedelta(days=jours))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(nom)]), critical=False)
               .sign(cle, hashes.SHA256()))
    certificat, fichier_cle = cp.fichiers(root)
    certificat.parent.mkdir(parents=True, exist_ok=True)
    certificat.write_bytes(feuille.public_bytes(serialization.Encoding.PEM))
    fichier_cle.write_bytes((autre if cle_differente else cle).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return certificat, fichier_cle


def _c(root, nom=NOM):
    return {"data_root": str(root), "hostname": "serveur", "https_port": 8443, "kiosk_http_port": 8080,
            "web_port": 18080, "lan_ip": "192.168.1.200", "network": True, "url": "https://serveur:8443",
            "certificat_public": {"nom": nom, "cpanel_hote": "https://cpanel.example:2083", "cpanel_utilisateur": "u"},
            "certificat_public_jeton": "x"}


def test_certificat_valide_et_renouvellement(tmp_path):
    c = _c(tmp_path)
    assert cp.certificat_valide(c, tmp_path) is None and cp.a_renouveler(c, tmp_path)
    _certificat_signe(tmp_path, NOM)
    assert cp.certificat_valide(c, tmp_path) is not None and not cp.a_renouveler(c, tmp_path)
    _certificat_signe(tmp_path, NOM, debut_il_y_a=61)  # 29 jours restants sur 90
    assert cp.certificat_valide(c, tmp_path) is not None and cp.a_renouveler(c, tmp_path)
    _certificat_signe(tmp_path, NOM, debut_il_y_a=91)  # expiré
    assert cp.certificat_valide(c, tmp_path) is None
    _certificat_signe(tmp_path, "autre.cgbcreil.test")
    assert cp.certificat_valide(c, tmp_path) is None
    _certificat_signe(tmp_path, NOM, cle_differente=True)
    assert cp.certificat_valide(c, tmp_path) is None
    _certificat_signe(tmp_path, NOM, jours=6, debut_il_y_a=1)  # certificats courts
    assert not cp.a_renouveler(c, tmp_path)
    _certificat_signe(tmp_path, NOM, jours=6, debut_il_y_a=4)
    assert cp.a_renouveler(c, tmp_path)


def test_caddyfile_repli_sur_l_autorite_du_centre(tmp_path):
    from desktop.runtime import write_caddy
    c = _c(tmp_path)
    texte = write_caddy(c, tmp_path).read_text(encoding="utf-8")
    assert f"https://{NOM}:8443" in texte.split(" tls internal")[0], "sans certificat : autorité du centre"
    assert " tls \"" not in texte
    _certificat_signe(tmp_path, NOM)
    texte = write_caddy(c, tmp_path).read_text(encoding="utf-8")
    assert f"https://{NOM}:8443" not in texte.split(" tls internal")[0]
    assert f"https://{NOM}:8443 {{\n tls " in texte
    _certificat_signe(tmp_path, NOM, debut_il_y_a=91)
    assert f"https://{NOM}:8443" in write_caddy(c, tmp_path).read_text(encoding="utf-8").split(" tls internal")[0]


def test_nom_public_hors_autorite_du_centre_et_accepte(tmp_path):
    from flask import Flask
    from desktop.runtime import adresse_publique, hotes_de_confiance, hotes_supplementaires, write_caddy
    c = _c(tmp_path)
    c["hotes_supplementaires"] = [NOM, "10.8.0.4"]  # saisi aussi dans les adresses : ignoré là
    assert hotes_supplementaires(c) == ["10.8.0.4"]
    from desktop.runtime import fichiers_autorite, noms_hors_autorite
    write_caddy(c, tmp_path)
    assert noms_hors_autorite(c, fichiers_autorite(tmp_path)[0]) == []
    app = Flask(__name__)
    app.config["TRUSTED_HOSTS"] = hotes_de_confiance(c)
    app.add_url_rule("/", "a", lambda: "ok")
    assert app.test_client().get("/", headers={"Host": f"{NOM}:8443"}).status_code == 200
    assert adresse_publique(c) == f"https://{NOM}:8443"
    del c["certificat_public"]
    assert adresse_publique(c) == "https://serveur:8443"


def test_nom_invalide_refuse(tmp_path):
    for nom in ("localhost", "gestion..cgbcreil.com", "192.168.1.200", "-x.cgbcreil.com"):
        with pytest.raises(cp.ErreurCertificat, match="pas un nom de domaine public valide"):
            cp.obtenir(_c(tmp_path, nom), tmp_path, cpanel=object())


def test_adresses_http_refusees():
    with pytest.raises(cp.ErreurCertificat, match="https:// obligatoire"):
        cp._https("http://cpanel.example:2083/json-api/cpanel")
    assert cp.CPanel("mon-serveur.o2switch.net", "u", "t").hote == "https://mon-serveur.o2switch.net:2083"
    with pytest.raises(cp.ErreurCertificat, match="jeton"):
        cp.CPanel("mon-serveur.o2switch.net", "u", "")


def test_rien_a_faire_sans_nom_public(tmp_path):
    assert cp.executer({"data_root": str(tmp_path)}) == 0
    assert not cp.fichier_etat(tmp_path).exists()


# ---------------------------------------------------------------------------
# Alerte dans l'application (administrateurs)
# ---------------------------------------------------------------------------

def _etat(dossier, **valeurs):
    runtime = dossier / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "certificat-public.json").write_text(json.dumps({"nom": NOM, **valeurs}), encoding="utf-8")


def test_alerte_expiration_certificat(tmp_path, monkeypatch):
    from app.services.certificat_https import alerte_certificat
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    maintenant = datetime.datetime.now(datetime.timezone.utc)

    def dans(jours):
        return (maintenant + datetime.timedelta(days=jours)).isoformat()

    assert alerte_certificat() is None  # jamais configuré
    _etat(tmp_path, expire_le=dans(60), erreur=None)
    assert alerte_certificat() is None
    _etat(tmp_path, expire_le=dans(40), erreur="cPanel injoignable")
    assert alerte_certificat() is None, "échec isolé loin de l'échéance : la tâche réessaie chaque nuit"
    _etat(tmp_path, expire_le=dans(20), erreur="cPanel refuse l'identifiant ou le jeton d'API.")
    message = alerte_certificat()
    assert NOM in message and "renouvellement" in message and "jeton" in message
    _etat(tmp_path, expire_le=dans(10), erreur=None)
    assert "sans avoir été renouvelé" in alerte_certificat()
    _etat(tmp_path, expire_le=dans(-1), erreur=None)
    assert "a expiré" in alerte_certificat()
    _etat(tmp_path, expire_le=None, erreur="premier essai raté")
    assert alerte_certificat() is None


def test_bandeau_reserve_aux_administrateurs(tmp_path, monkeypatch, admin_client, client):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    _etat(tmp_path, expire_le=(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5)).isoformat())
    page = admin_client.get("/", follow_redirects=True).get_data(as_text=True)
    assert 'data-alerte-certificat="1"' in page and NOM in page
    anonyme = client.get("/", follow_redirects=True).get_data(as_text=True)
    assert 'data-alerte-certificat' not in anonyme
