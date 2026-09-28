"""Nom d'accès ajouté après l'installation (ex. « gestion.cgb ») : hôtes de
confiance, Caddyfile et autorité HTTPS contrainte.

Situation constatée : nom ajouté à la main dans le Caddyfile seul → l'application
répond 400 « Host 'gestion.cgb:8443' is not trusted », et l'autorité contrainte
(NameConstraints fixées à la création) ne peut pas certifier ce nom.
"""
import datetime
import ipaddress
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

RACINE_DEPOT = Path(__file__).resolve().parents[1]

_BASE = {"hostname": "serveur", "https_port": 8443, "kiosk_http_port": 8080, "web_port": 18080,
         "lan_ip": "192.168.1.200"}


def _config(*hotes, **extra):
    c = dict(_BASE, hotes_supplementaires=list(hotes))
    c.update(extra)
    return c


def _chaine_valide(racine_pem, cle_pem, nom):
    """Signe intermédiaire + certificat de site comme Caddy, puis vérifie la
    chaîne comme un navigateur (contraintes de noms comprises)."""
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
    racine = x509.load_pem_x509_certificate(racine_pem)
    cle_racine = serialization.load_pem_private_key(cle_pem, None)
    maintenant = datetime.datetime.now(datetime.timezone.utc)

    def emettre(sujet, cle_publique, emetteur, cle_emetteur, ca, san=None, aki=None):
        b = (x509.CertificateBuilder().subject_name(sujet).issuer_name(emetteur.subject if emetteur else sujet)
             .public_key(cle_publique).serial_number(x509.random_serial_number())
             .not_valid_before(maintenant - datetime.timedelta(minutes=5))
             .not_valid_after(maintenant + datetime.timedelta(days=1))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
             .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=ca, crl_sign=ca, content_commitment=False,
                                          key_encipherment=False, data_encipherment=False, key_agreement=False,
                                          encipher_only=False, decipher_only=False), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle_publique), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(cle_emetteur.public_key()), critical=False))
        if san:
            b = b.add_extension(x509.SubjectAlternativeName(san), critical=True)  # sujet vide, comme Caddy
            b = b.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        return b.sign(cle_emetteur, hashes.SHA256())

    cle_inter = ec.generate_private_key(ec.SECP256R1())
    inter = emettre(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "intermediaire")]),
                    cle_inter.public_key(), racine, cle_racine, True)
    cle_site = ec.generate_private_key(ec.SECP256R1())
    site = emettre(x509.Name([]), cle_site.public_key(), inter, cle_inter, False, san=[x509.DNSName(nom)])
    verificateur = PolicyBuilder().store(Store([racine])).build_server_verifier(x509.DNSName(nom))
    try:
        verificateur.verify(site, [inter])
        return True
    except VerificationError:
        return False


def test_nom_ajoute_accepte_par_l_application():
    """Le 400 « not trusted » vient des hôtes de confiance, pas de Caddy."""
    from flask import Flask
    from desktop.runtime import hotes_de_confiance
    app = Flask(__name__)
    app.config["TRUSTED_HOSTS"] = hotes_de_confiance(_config("Gestion.CGB"))

    @app.get("/")
    def accueil():
        from flask import request
        return request.host

    client = app.test_client()
    assert client.get("/", headers={"Host": "gestion.cgb:8443"}).status_code == 200
    assert client.get("/", headers={"Host": "192.168.1.200:8443"}).status_code == 200
    assert client.get("/", headers={"Host": "intrus.example:8443"}).status_code == 400
    # Sans déclaration (nom ajouté seulement dans le Caddyfile) : refus constaté chez Antoine.
    app.config["TRUSTED_HOSTS"] = hotes_de_confiance(_config())
    assert client.get("/", headers={"Host": "gestion.cgb:8443"}).status_code == 400


def test_autorite_existante_ne_couvre_pas_un_nom_ajoute(tmp_path):
    from desktop.runtime import fichiers_autorite, noms_hors_autorite, write_caddy
    write_caddy(_config(), tmp_path)
    certificat, cle = fichiers_autorite(tmp_path)
    assert noms_hors_autorite(_config(), certificat) == []
    assert noms_hors_autorite(_config("gestion.cgb", "10.8.0.4", "203.0.113.9"), certificat) == ["gestion.cgb", "203.0.113.9"]
    # Sous-domaine du nom du serveur : déjà permis (RFC 5280).
    assert noms_hors_autorite(_config("poste.serveur"), certificat) == []
    # Preuve « navigateur » : l'autorité actuelle ne peut pas certifier gestion.cgb.
    assert not _chaine_valide(certificat.read_bytes(), cle.read_bytes(), "gestion.cgb")
    assert _chaine_valide(certificat.read_bytes(), cle.read_bytes(), "serveur")


def test_sans_accord_l_autorite_deployee_est_gardee(tmp_path):
    from desktop.runtime import fichiers_autorite, write_caddy
    write_caddy(_config(), tmp_path)
    certificat, _ = fichiers_autorite(tmp_path)
    avant = certificat.read_bytes()
    texte = write_caddy(_config("gestion.cgb"), tmp_path).read_text(encoding="utf-8")
    assert "https://gestion.cgb:8443" in texte
    assert certificat.read_bytes() == avant, "jamais de remplacement silencieux : les postes perdraient HTTPS"
    assert not (certificat.parent / "remplacees").exists()


def test_remplacement_consenti_couvre_le_nom_et_met_l_ancienne_de_cote(tmp_path):
    from desktop.runtime import fichiers_autorite, noms_hors_autorite, write_caddy
    write_caddy(_config(), tmp_path)
    certificat, cle = fichiers_autorite(tmp_path)
    ancienne_racine, ancienne_cle = certificat.read_bytes(), cle.read_bytes()
    # Ce que Caddy a produit sous l'ancienne autorité.
    local = tmp_path / "https" / "tls" / "pki" / "authorities" / "local"
    local.mkdir(parents=True)
    (local / "intermediate.crt").write_text("inter")
    (local / "intermediate.key").write_text("cle inter")
    (local / "root.crt").write_text("copie de la racine par Caddy")
    sites = tmp_path / "https" / "tls" / "certificates" / "local" / "serveur"
    sites.mkdir(parents=True)
    (sites / "serveur.crt").write_text("site")

    c = _config("gestion.cgb", renouveler_autorite=True)
    texte = write_caddy(c, tmp_path).read_text(encoding="utf-8")
    assert "https://gestion.cgb:8443" in texte and " pki {" in texte
    nouvelle = certificat.read_bytes()
    assert nouvelle != ancienne_racine and noms_hors_autorite(c, certificat) == []
    assert _chaine_valide(nouvelle, cle.read_bytes(), "gestion.cgb")
    assert _chaine_valide(nouvelle, cle.read_bytes(), "serveur")
    # Toujours contrainte : pas de site de l'Internet.
    assert not _chaine_valide(nouvelle, cle.read_bytes(), "www.impots.gouv.fr")
    contraintes = x509.load_pem_x509_certificate(nouvelle).extensions.get_extension_for_class(x509.NameConstraints)
    assert contraintes.critical
    assert not any(isinstance(n, x509.IPAddress) and n.value.prefixlen == 0 for n in contraintes.value.permitted_subtrees)

    archives = list((certificat.parent / "remplacees").iterdir())
    assert len(archives) == 1
    archive = archives[0]
    assert (archive / "racine.crt").read_bytes() == ancienne_racine
    assert (archive / "racine.key").read_bytes() == ancienne_cle
    assert (archive / "intermediate.crt").exists() and (archive / "intermediate.key").exists()
    assert (archive / "local" / "serveur" / "serveur.crt").exists()
    assert not (local / "intermediate.crt").exists(), "Caddy doit recréer l'intermédiaire sous la nouvelle autorité"
    assert not (tmp_path / "https" / "tls" / "certificates" / "local").exists()

    # Accord resté dans la configuration (interruption) : aucun nouveau remplacement.
    write_caddy(c, tmp_path)
    assert certificat.read_bytes() == nouvelle and len(list((certificat.parent / "remplacees").iterdir())) == 1


def test_ancienne_autorite_caddy_non_contrainte_inchangee(tmp_path):
    """Installation antérieure à l'audit 6.7 : autorité Caddy sans contrainte,
    qui couvre déjà tout nom ; rien n'est remplacé, même avec l'accord."""
    from desktop.runtime import fichiers_autorite, write_caddy
    ancienne = tmp_path / "https" / "tls" / "pki" / "authorities" / "local" / "root.crt"
    ancienne.parent.mkdir(parents=True)
    ancienne.write_text("déjà déployée sur les postes")
    texte = write_caddy(_config("gestion.cgb", renouveler_autorite=True), tmp_path).read_text(encoding="utf-8")
    assert "https://gestion.cgb:8443" in texte and " pki {" not in texte
    assert not fichiers_autorite(tmp_path)[0].exists() and ancienne.read_text() == "déjà déployée sur les postes"


@pytest.mark.parametrize("hotes,code", [((), 0), (("gestion.cgb",), 3), (("10.8.0.4",), 0)])
def test_mode_verifier_autorite(tmp_path, hotes, code):
    from desktop.runtime import write_caddy
    write_caddy(_config(), tmp_path)
    c = _config(*hotes, data_root=str(tmp_path))
    fini = subprocess.run([sys.executable, "-B", str(RACINE_DEPOT / "desktop" / "runtime.py"), "--verifier-autorite"],
                          input=json.dumps(c).encode(), capture_output=True, cwd=RACINE_DEPOT, timeout=120)
    assert fini.returncode == code, fini.stderr.decode(errors="replace")
    if code:
        assert fini.stdout.decode().strip() == "gestion.cgb"


def test_mode_verifier_autorite_sans_autorite_contrainte(tmp_path):
    c = _config("gestion.cgb", data_root=str(tmp_path))
    fini = subprocess.run([sys.executable, "-B", str(RACINE_DEPOT / "desktop" / "runtime.py"), "--verifier-autorite"],
                          input=json.dumps(c).encode(), capture_output=True, cwd=RACINE_DEPOT, timeout=120)
    assert fini.returncode == 0, fini.stderr.decode(errors="replace")
