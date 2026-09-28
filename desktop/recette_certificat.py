"""Banc d'essai du certificat reconnu : Pebble (serveur ACME de test de
Let's Encrypt), pebble-challtestsrv (DNS de test) et un faux cPanel en HTTPS
(API 2 ZoneEdit, mêmes appels qu'acme.sh dns_cpanel).

Utilisé par tests/test_certificat_public.py et par la recette Windows de la
CI (jamais livré dans l'installateur) :

    python recette_certificat.py <dossier>   # écrit <dossier>/banc.json, tourne jusqu'à <dossier>/stop
"""
from __future__ import annotations

import contextlib
import datetime
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

NOM = "gestion.cgbcreil.test"
ZONE = "cgbcreil.test"
UTILISATEUR, JETON = "cgbcreil", "JETON-DE-TEST-0123456789"


def binaire(nom):
    exe = nom + (".exe" if os.name == "nt" else "")
    dossiers = [os.environ.get("PEBBLE_BIN_DIR"), str(Path.home() / "go" / "bin"),
                os.environ.get("GOPATH") and str(Path(os.environ["GOPATH"]) / "bin"), "/tmp/claude-0/go/bin"]
    for dossier in filter(None, dossiers):
        if (Path(dossier) / exe).exists():
            return str(Path(dossier) / exe)
    return shutil.which(nom)


def port_libre(type_=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, type_) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def autorite_et_serveur(dossier: Path, nom_serveur="localhost"):
    """Autorité de test + certificat serveur pour 127.0.0.1/localhost."""
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    cle_ca = ec.generate_private_key(ec.SECP256R1())
    sujet_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"autorite de test {nom_serveur}")])
    ca = (x509.CertificateBuilder().subject_name(sujet_ca).issuer_name(sujet_ca).public_key(cle_ca.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(maintenant - datetime.timedelta(hours=1))
          .not_valid_after(maintenant + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle_ca.public_key()), critical=False)
          .sign(cle_ca, hashes.SHA256()))
    cle = ec.generate_private_key(ec.SECP256R1())
    serveur = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nom_serveur)]))
               .issuer_name(sujet_ca).public_key(cle.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(maintenant - datetime.timedelta(hours=1)).not_valid_after(maintenant + datetime.timedelta(days=2))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(cle_ca.public_key()), critical=False)
               .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
               .sign(cle_ca, hashes.SHA256()))
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (dossier / "cert.pem").write_bytes(serveur.public_bytes(serialization.Encoding.PEM))
    (dossier / "key.pem").write_bytes(cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    return dossier / "ca.pem", dossier / "cert.pem", dossier / "key.pem"


def attendre_port(port, delai=30):
    fin = time.time() + delai
    while time.time() < fin:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {port} jamais ouvert")


class FauxCPanel:
    """cPanel API 2 ZoneEdit, en HTTPS. Publie les TXT dans le DNS de test
    (éventuellement avec retard, comme un serveur DNS secondaire)."""

    def __init__(self, dossier, gestion_dns=None, retard=0.0, zones=(ZONE,)):
        self.gestion_dns, self.retard, self.zones = gestion_dns, retard, list(zones)
        self.enregistrements, self.appels, self.ligne = [], [], 100
        self.ca, cert, cle = autorite_et_serveur(Path(dossier), "cpanel")
        faux = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                faux.appels.append(q.get("cpanel_jsonapi_func"))
                if self.headers.get("Authorization") != f"cpanel {UTILISATEUR}:{JETON}":
                    self.send_response(401)
                    self.end_headers()
                    return
                corps = json.dumps({"cpanelresult": faux.traiter(q)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), Gestionnaire)
        contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexte.load_cert_chain(cert, cle)
        self.serveur.socket = contexte.wrap_socket(self.serveur.socket, server_side=True)
        self.url = f"https://127.0.0.1:{self.serveur.server_address[1]}"
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    def _dns(self, action, fqdn, valeur):
        if not self.gestion_dns:
            return
        corps = json.dumps({"host": fqdn + ".", "value": valeur}).encode()
        requete = urllib.request.Request(f"http://127.0.0.1:{self.gestion_dns}/{action}", data=corps, method="POST")
        with urllib.request.urlopen(requete, timeout=5):
            pass

    def traiter(self, q):
        fonction = q["cpanel_jsonapi_func"]
        if fonction == "fetchzones":
            return {"data": [{"zones": {z: ["; cPanel first line"] for z in self.zones}}], "event": {"result": 1}}
        if fonction == "add_zone_record":
            if q["domain"] not in self.zones:
                return {"data": [{"result": {"status": 0, "statusmsg": "zone inconnue"}}]}
            self.ligne += 1
            fqdn = f"{q['name']}.{q['domain']}"
            self.enregistrements.append({"line": self.ligne, "name": fqdn + ".", "type": "TXT", "txtdata": q["txtdata"]})
            threading.Timer(self.retard, self._dns, ("set-txt", fqdn, q["txtdata"])).start()
            return {"data": [{"result": {"status": 1, "statusmsg": "", "newserial": "2026092801"}}]}
        if fonction == "fetchzone_records":
            return {"data": [e for e in self.enregistrements if e["type"] == q.get("type", e["type"])]}
        if fonction == "remove_zone_record":
            ligne = int(q["line"])
            retires = [e for e in self.enregistrements if e["line"] == ligne]
            if not retires:
                return {"data": [{"result": {"status": 0, "statusmsg": "ligne inconnue"}}]}
            self.enregistrements = [e for e in self.enregistrements if e["line"] != ligne]
            self._dns("clear-txt", retires[0]["name"].rstrip("."), "")
            return {"data": [{"result": {"status": 1, "statusmsg": "", "newserial": "2026092802"}}]}
        return {"error": f"fonction inconnue {fonction}"}

    def fermer(self):
        self.serveur.shutdown()
        self.serveur.server_close()


@contextlib.contextmanager
def pebble(dossier: Path):
    """Pebble + DNS de test ; rend les adresses utiles."""
    pebble_exe, challtestsrv = binaire("pebble"), binaire("pebble-challtestsrv")
    if not (pebble_exe and challtestsrv):
        raise FileNotFoundError("Pebble non installé")
    dossier.mkdir(parents=True, exist_ok=True)
    dns, gestion = port_libre(socket.SOCK_DGRAM), port_libre()
    acme, gestion_pebble = port_libre(), port_libre()
    ca, cert, cle = autorite_et_serveur(dossier / "pebble-tls")
    configuration = dossier / "pebble.json"
    configuration.write_text(json.dumps({"pebble": {
        "listenAddress": f"127.0.0.1:{acme}", "managementListenAddress": f"127.0.0.1:{gestion_pebble}",
        "certificate": str(cert), "privateKey": str(cle), "httpPort": port_libre(), "tlsPort": port_libre(),
        "ocspResponderURL": "", "externalAccountBindingRequired": False}}))
    journal = open(dossier / "pebble.log", "wb")
    processus = [
        subprocess.Popen([challtestsrv, "-dnsserver", f"127.0.0.1:{dns}", "-management", f"127.0.0.1:{gestion}",
                          "-http01", "", "-https01", "", "-tlsalpn01", "", "-doh", "", "-defaultIPv4", "127.0.0.1",
                          "-defaultIPv6", ""], stdout=journal, stderr=journal),
        # Nonces refusés au hasard (5 % par défaut) : le client doit les rejouer.
        subprocess.Popen([pebble_exe, "-config", str(configuration), "-dnsserver", f"127.0.0.1:{dns}"],
                         stdout=journal, stderr=journal, env={**os.environ, "PEBBLE_VA_NOSLEEP": "1"}),
    ]
    try:
        attendre_port(gestion)
        attendre_port(acme)
        yield {"repertoire": f"https://127.0.0.1:{acme}/dir", "ca": str(ca), "dns": ("127.0.0.1", dns),
               "gestion_dns": gestion, "gestion_pebble": gestion_pebble, "journal": dossier / "pebble.log"}
    finally:
        for p in processus:
            p.terminate()
            p.wait(timeout=10)
        journal.close()


def racines_pebble(info):
    contexte = ssl.create_default_context(cafile=info["ca"])
    racines = []
    for i in range(3):
        try:
            with urllib.request.urlopen(f"https://127.0.0.1:{info['gestion_pebble']}/roots/{i}", context=contexte) as r:
                racines.append(x509.load_pem_x509_certificate(r.read()))
        except Exception:
            break
    return racines


def main(dossier):
    dossier = Path(dossier)
    with pebble(dossier) as info:
        cpanel = FauxCPanel(dossier / "cpanel", info["gestion_dns"])
        racine = dossier / "pebble-racine.pem"
        racine.write_bytes(b"".join(r.public_bytes(serialization.Encoding.PEM) for r in racines_pebble(info)))
        banc = {"nom": NOM, "utilisateur": UTILISATEUR, "jeton": JETON, "cpanel": cpanel.url, "cpanel_ca": str(cpanel.ca),
                "acme": info["repertoire"], "acme_ca": info["ca"], "dns": f"{info['dns'][0]}:{info['dns'][1]}",
                "racine": str(racine)}
        (dossier / "banc.json").write_text(json.dumps(banc), encoding="utf-8")
        while not (dossier / "stop").exists():
            time.sleep(0.5)
        cpanel.fermer()


if __name__ == "__main__":
    main(sys.argv[1])
