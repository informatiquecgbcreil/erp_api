"""Connecter un appareil : QR codes et alerte de sécurité.

Ce que ces tests garantissent :

- la page donne la bonne adresse à la tablette (kiosque, sans certificat)
  et au collègue (page d'installation), et le QR code accepte ces adresses ;
- une adresse qui ne marche que sur le serveur (127.0.0.1) est signalée ;
- le certificat du centre déposé par l'installateur Windows est lu, proposé
  au téléchargement à l'identique, avec l'empreinte que Windows affiche ;
- une autorité ancienne, non bridée, n'est conseillée que sur les appareils
  du centre ; avec un certificat reconnu, rien à installer ;
- la façade « hors les murs » ne sert pas le certificat.

Le certificat est fabriqué par le test comme le fait l'installateur
(desktop/runtime.py, autorite_contrainte), puis exporté en DER comme le fait
MonCentreSocial.cs.
"""
import datetime
import hashlib
import ipaddress
import json

import pytest


def _autorite(bridee: bool) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cle = ec.generate_private_key(ec.SECP256R1())
    sujet = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Mon Centre Social - autorite locale serveur-test")])
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    construction = (x509.CertificateBuilder().subject_name(sujet).issuer_name(sujet)
                    .public_key(cle.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(maintenant).not_valid_after(maintenant + datetime.timedelta(days=3650))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True))
    if bridee:
        construction = construction.add_extension(x509.NameConstraints(
            permitted_subtrees=[x509.DNSName("serveur-test"),
                                x509.IPAddress(ipaddress.ip_network("192.168.0.0/16"))],
            excluded_subtrees=None), critical=True)
    return construction.sign(cle, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


@pytest.fixture
def serveur(app, tmp_path, monkeypatch):
    """Un serveur Windows en réseau : adresse HTTPS, kiosque en http:// sur le LAN."""
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://serveur-test:8443")
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_BASE_URL", "http://192.168.1.20:8080")
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "")

    def deposer(contenu: bytes):
        (tmp_path / "public").mkdir(exist_ok=True)
        (tmp_path / "public" / "Certificat-du-centre.cer").write_bytes(contenu)
        return contenu

    return deposer


def test_tablette_et_collegue_recoivent_les_adresses_du_reseau(serveur, admin_client):
    page = admin_client.get("/admin/appareils").get_data(as_text=True)
    assert "http://192.168.1.20:8080/kiosk/" in page
    assert "https://serveur-test:8443/installer" in page
    assert "Aucun certificat à installer" in page
    assert "ne fonctionne <strong>que sur le serveur" not in page
    # Les QR codes visent ces adresses, et le générateur les accepte.
    for adresse in ("http://192.168.1.20:8080/kiosk/", "https://serveur-test:8443/installer"):
        assert f"/launcher/qr?u={adresse}" in page
        qr = admin_client.get("/launcher/qr", query_string={"u": adresse})
        assert qr.status_code == 200 and qr.mimetype == "image/svg+xml"


def test_adresse_du_serveur_seul_signalee(app, admin_client, monkeypatch):
    """Installation « ce poste uniquement » : les QR codes ne mèneraient nulle part."""
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_BASE_URL", "")
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "")
    page = admin_client.get("/admin/appareils").get_data(as_text=True)
    assert "ne fonctionne <strong>que sur le serveur" in page
    assert "Configurer Mon Centre Social" in page


def test_certificat_bride_propose_avec_l_empreinte_de_windows(serveur, admin_client, client):
    contenu = serveur(_autorite(bridee=True))
    sha1 = hashlib.sha1(contenu).hexdigest().upper()
    empreinte = " ".join(sha1[i:i + 8] for i in range(0, 40, 8))

    page = admin_client.get("/admin/appareils").get_data(as_text=True)
    assert empreinte in page
    assert "Autorités de certification racines de confiance" in page
    assert "Il ne vaut que pour les adresses du centre" in page
    assert "installation ancienne" not in page

    # Le collègue, sans compte ouvert, trouve le guide et le fichier.
    installation = client.get("/installer").get_data(as_text=True)
    assert "Une alerte de sécurité s" in installation and empreinte in installation
    assert "/installer/certificat-du-centre.cer" in installation
    r = client.get("/installer/certificat-du-centre.cer")
    assert r.status_code == 200
    assert r.data == contenu
    assert r.mimetype == "application/x-x509-ca-cert"
    assert 'filename="Certificat-du-centre.cer"' in r.headers["Content-Disposition"]


def test_certificat_pem_servi_en_der(serveur, client):
    """Un certificat déposé à la main au format texte (PEM) est servi comme Windows l'exporte."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    der = _autorite(bridee=True)
    serveur(x509.load_der_x509_certificate(der).public_bytes(Encoding.PEM))
    assert client.get("/installer/certificat-du-centre.cer").data == der


def test_ancienne_autorite_reservee_aux_appareils_du_centre(serveur, admin_client, client):
    serveur(_autorite(bridee=False))
    for page in (admin_client.get("/admin/appareils"), client.get("/installer")):
        texte = page.get_data(as_text=True)
        assert "installation ancienne" in texte and "appareils du centre" in texte
        assert "Il ne vaut que pour les adresses du centre" not in texte


def test_certificat_reconnu_rien_a_installer(serveur, admin_client, client, app, monkeypatch, tmp_path):
    serveur(_autorite(bridee=True))
    monkeypatch.setitem(app.config, "PUBLIC_BASE_URL", "https://gestion.exemple.org:8443")
    (tmp_path / "runtime").mkdir()
    expire = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=80)).isoformat()
    (tmp_path / "runtime" / "certificat-public.json").write_text(
        json.dumps({"nom": "gestion.exemple.org", "expire_le": expire}), encoding="utf-8")

    page = admin_client.get("/admin/appareils").get_data(as_text=True)
    assert "certificat reconnu" in page and "gestion.exemple.org" in page
    assert "Autorités de certification racines de confiance" not in page
    assert "Une alerte de sécurité s" not in client.get("/installer").get_data(as_text=True)


def test_sans_certificat_ni_guide_ni_fichier(serveur, admin_client, client, tmp_path):
    assert client.get("/installer/certificat-du-centre.cer").status_code == 404
    assert "Une alerte de sécurité s" not in client.get("/installer").get_data(as_text=True)
    assert "pas été trouvé sur ce serveur" in admin_client.get("/admin/appareils").get_data(as_text=True)
    # Un fichier qui n'est pas un certificat n'est ni servi ni décrit.
    serveur(b"ceci n'est pas un certificat")
    assert client.get("/installer/certificat-du-centre.cer").status_code == 404
    assert "pas été trouvé sur ce serveur" in admin_client.get("/admin/appareils").get_data(as_text=True)


def test_la_facade_hors_les_murs_ne_sert_pas_le_certificat(serveur, client, app, monkeypatch):
    serveur(_autorite(bridee=True))
    monkeypatch.setitem(app.config, "KIOSK_PUBLIC_HOST", "kiosque.exemple.org")
    r = client.get("/installer/certificat-du-centre.cer", headers={"Tailscale-Funnel-Request": "?1"})
    assert r.status_code == 403


def test_reserve_aux_administrateurs(client):
    assert client.get("/admin/appareils").status_code in (302, 401, 403)


def test_trouvable_en_affichage_simple_et_aide(serveur, admin_client):
    """L'affichage simple (par défaut) n'a pas ces pages au menu : la santé du système y mène."""
    from app.aide.contenu import AIDE_PAGES

    sante = admin_client.get("/admin/sante").get_data(as_text=True)
    assert "/admin/appareils" in sante and "/admin/entrainement" in sante
    assert "admin.connecter_appareil" in AIDE_PAGES
