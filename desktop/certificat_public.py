"""Certificat HTTPS reconnu par tous les appareils (Let's Encrypt), sans rien
installer sur les postes, téléphones ni tablettes.

Le nom (ex. gestion.cgbcreil.com) appartient à un domaine dont la zone DNS est
gérée par cPanel (O2Switch). La preuve de propriété passe par le DNS (défi
« dns-01 ») : le serveur n'a besoin d'aucun port ouvert sur Internet et
l'application reste joignable seulement depuis le réseau du centre.

Aucune dépendance ajoutée : client ACME (RFC 8555) minimal sur ``cryptography``
et ``urllib``, API 2 de cPanel (celle qu'utilise acme.sh ``dns_cpanel``),
requête DNS directe aux serveurs faisant autorité pour attendre la publication.

Répartition des secrets : le jeton cPanel et la clé du compte ACME ne sont lus
que par ce module, lancé par les administrateurs ou la tâche planifiée SYSTEM ;
le service web ne les voit jamais. La clé du certificat est placée dans
``https/public`` (lisible par le seul service HTTPS).
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import random
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LETSENCRYPT = "https://acme-v02.api.letsencrypt.org/directory"
#: Serveurs publics interrogés pour trouver les serveurs DNS de la zone.
RESOLVEURS_PUBLICS = (("1.1.1.1", 53), ("8.8.8.8", 53), ("9.9.9.9", 53))
USER_AGENT = "MonCentreSocial-certificat/1"

NOUVEAU = 10  # code de sortie : certificat écrit, le proxy doit être relancé


class ErreurCertificat(Exception):
    """Message destiné à la personne qui configure (français, sans secret)."""


# ---------------------------------------------------------------------------
# Configuration et fichiers
# ---------------------------------------------------------------------------

def reglages(c) -> dict | None:
    brut = c.get("certificat_public")
    if not isinstance(brut, dict) or not str(brut.get("nom") or "").strip():
        return None
    return brut


def nom_public(c) -> str:
    r = reglages(c)
    return str(r["nom"]).strip().lower().rstrip(".") if r else ""


def fichiers(root):
    dossier = Path(root) / "https" / "public"
    return dossier / "certificat.crt", dossier / "certificat.key"


def fichier_etat(root) -> Path:
    # Lisible par le service web (alerte d'expiration), sans aucun secret.
    return Path(root) / "runtime" / "certificat-public.json"


def _ecrire_atomique(chemin: Path, donnees: bytes) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(chemin.name + ".new")
    temporaire.write_bytes(donnees)
    os.replace(temporaire, chemin)


def certificat_valide(c, root, *, maintenant=None):
    """(certificat, clé, expiration) si le certificat enregistré couvre le nom
    configuré, n'est pas expiré et correspond à sa clé ; sinon None."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    nom = nom_public(c)
    certificat, cle = fichiers(root)
    if not nom or not certificat.exists() or not cle.exists():
        return None
    try:
        feuille = x509.load_pem_x509_certificates(certificat.read_bytes())[0]
        privee = serialization.load_pem_private_key(cle.read_bytes(), None)
        noms = feuille.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except (ValueError, x509.ExtensionNotFound, IndexError):
        return None
    maintenant = maintenant or datetime.datetime.now(datetime.timezone.utc)
    publique = serialization.PublicFormat.SubjectPublicKeyInfo
    if (nom not in [n.lower() for n in noms]
            or not (feuille.not_valid_before_utc <= maintenant < feuille.not_valid_after_utc)
            or privee.public_key().public_bytes(serialization.Encoding.DER, publique)
            != feuille.public_key().public_bytes(serialization.Encoding.DER, publique)):
        return None
    return certificat, cle, feuille.not_valid_after_utc


def a_renouveler(c, root, *, maintenant=None) -> bool:
    """Renouvellement au dernier tiers de la validité (30 jours sur 90), ou
    au moins 10 jours avant l'échéance pour les certificats courts."""
    from cryptography import x509
    valide = certificat_valide(c, root, maintenant=maintenant)
    if valide is None:
        return True
    feuille = x509.load_pem_x509_certificates(valide[0].read_bytes())[0]
    maintenant = maintenant or datetime.datetime.now(datetime.timezone.utc)
    duree = feuille.not_valid_after_utc - feuille.not_valid_before_utc
    marge = max(duree / 3, datetime.timedelta(days=10)) if duree > datetime.timedelta(days=20) else duree / 2
    return feuille.not_valid_after_utc - maintenant <= marge


def _noter_etat(root, **valeurs) -> None:
    chemin = fichier_etat(root)
    try:
        etat = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        etat = {}
    etat.update(valeurs)
    _ecrire_atomique(chemin, json.dumps(etat, ensure_ascii=False, indent=1).encode("utf-8"))


# ---------------------------------------------------------------------------
# HTTPS (vérification toujours active ; autorité supplémentaire pour les tests)
# ---------------------------------------------------------------------------

def _contexte(ca=None) -> ssl.SSLContext:
    if ca:
        return ssl.create_default_context(cafile=str(ca))
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi est livré
        return ssl.create_default_context()


def _https(url, *, methode="GET", donnees=None, entetes=None, ca=None, delai=30):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ErreurCertificat(f"Adresse refusée (https:// obligatoire) : {url}")
    requete = urllib.request.Request(url, data=donnees, method=methode,
                                     headers={"User-Agent": USER_AGENT, **(entetes or {})})
    try:
        with urllib.request.urlopen(requete, timeout=delai, context=_contexte(ca)) as reponse:
            return reponse.status, dict(reponse.headers.items()), reponse.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


# ---------------------------------------------------------------------------
# cPanel (API 2 ZoneEdit, jeton d'API)
# ---------------------------------------------------------------------------

class CPanel:
    def __init__(self, hote, utilisateur, jeton, *, ca=None):
        hote = str(hote or "").strip().rstrip("/")
        if not hote:
            raise ErreurCertificat("Indiquez l'adresse du cPanel (ex. https://votre-serveur.o2switch.net:2083).")
        if "://" not in hote:
            hote = "https://" + hote
        if urllib.parse.urlsplit(hote).port is None:
            hote += ":2083"
        if not utilisateur or not jeton:
            raise ErreurCertificat("Indiquez l'identifiant cPanel et le jeton d'API.")
        self.hote, self.utilisateur, self.jeton, self.ca = hote, str(utilisateur).strip(), str(jeton).strip(), ca

    def _appel(self, fonction, **parametres):
        requete = {"cpanel_jsonapi_apiversion": "2", "cpanel_jsonapi_module": "ZoneEdit",
                   "cpanel_jsonapi_func": fonction, **parametres}
        url = f"{self.hote}/json-api/cpanel?{urllib.parse.urlencode(requete)}"
        try:
            statut, _, corps = _https(url, entetes={"Authorization": f"cpanel {self.utilisateur}:{self.jeton}"}, ca=self.ca)
        except (OSError, ssl.SSLError) as exc:
            raise ErreurCertificat(f"cPanel injoignable ({self.hote}) : {exc}") from None
        if statut in (401, 403):
            raise ErreurCertificat("cPanel refuse l'identifiant ou le jeton d'API.")
        if statut != 200:
            raise ErreurCertificat(f"cPanel répond {statut}.")
        try:
            resultat = json.loads(corps)["cpanelresult"]
        except (ValueError, KeyError, TypeError):
            raise ErreurCertificat("Réponse inattendue de cPanel (identifiant ou jeton incorrect ?).") from None
        if resultat.get("error"):
            raise ErreurCertificat(f"cPanel : {resultat['error']}")
        return resultat.get("data") or []

    def zone(self, nom) -> str:
        zones = {}
        for bloc in self._appel("fetchzones"):
            zones.update((bloc or {}).get("zones") or {})
        candidates = [z.lower().rstrip(".") for z in zones if nom == z.lower().rstrip(".") or nom.endswith("." + z.lower().rstrip("."))]
        if not candidates:
            raise ErreurCertificat(f"Aucune zone DNS de ce cPanel ne contient « {nom} ».")
        return max(candidates, key=len)

    @staticmethod
    def _verifier(data, action):
        resultat = (data[0] or {}).get("result") if data else None
        if not resultat or not resultat.get("status"):
            detail = (resultat or {}).get("statusmsg") or "sans détail"
            raise ErreurCertificat(f"cPanel n'a pas pu {action} l'enregistrement DNS : {detail}")

    def ajouter_txt(self, zone, fqdn, valeur):
        relatif = fqdn[: -len(zone) - 1] if fqdn.endswith("." + zone) else fqdn
        self._verifier(self._appel("add_zone_record", domain=zone, name=relatif, type="TXT",
                                   txtdata=valeur, ttl="60"), "ajouter")

    def retirer_txt(self, zone, fqdn, valeur):
        for enregistrement in self._appel("fetchzone_records", domain=zone, type="TXT"):
            nom = str(enregistrement.get("name") or "").lower().rstrip(".")
            texte = str(enregistrement.get("txtdata") or enregistrement.get("record") or "").strip('"')
            if nom == fqdn and texte == valeur and enregistrement.get("line") is not None:
                self._verifier(self._appel("remove_zone_record", domain=zone, line=str(enregistrement["line"])), "retirer")


# ---------------------------------------------------------------------------
# DNS : attendre que les serveurs faisant autorité publient la valeur
# ---------------------------------------------------------------------------

def _encoder_nom(nom):
    return b"".join(bytes([len(p)]) + p.encode("ascii") for p in nom.rstrip(".").split(".")) + b"\0"


def _lire_nom(paquet, position):
    etiquettes, saut, suivant = [], False, None
    for _ in range(128):
        longueur = paquet[position]
        if longueur & 0xC0 == 0xC0:
            if not saut:
                suivant = position + 2
            position, saut = ((longueur & 0x3F) << 8) | paquet[position + 1], True
            continue
        position += 1
        if longueur == 0:
            break
        etiquettes.append(paquet[position:position + longueur].decode("ascii", "replace"))
        position += longueur
    return ".".join(etiquettes).lower(), (suivant if saut else position)


def requete_dns(nom, type_, serveur, port=53, *, delai=5.0, recursion=True):
    """Réponses (TXT : textes, NS : noms) d'un serveur DNS, par UDP."""
    code = {"TXT": 16, "NS": 2}[type_]
    identifiant = random.randrange(65536)
    question = struct.pack(">HHHHHH", identifiant, 0x0100 if recursion else 0, 1, 0, 0, 0) + _encoder_nom(nom) + struct.pack(">HH", code, 1)
    with socket.socket(socket.AF_INET6 if ":" in serveur else socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(delai)
        s.sendto(question, (serveur, port))
        while True:
            paquet, _ = s.recvfrom(4096)
            if len(paquet) >= 12 and struct.unpack(">H", paquet[:2])[0] == identifiant:
                break
    _, drapeaux, nq, nr, _, _ = struct.unpack(">HHHHHH", paquet[:12])
    if drapeaux & 0x000F not in (0, 3):
        raise OSError(f"erreur DNS {drapeaux & 0x000F}")
    position = 12
    for _ in range(nq):
        _, position = _lire_nom(paquet, position)
        position += 4
    resultats = []
    for _ in range(nr):
        _, position = _lire_nom(paquet, position)
        type_r, _, _, taille = struct.unpack(">HHIH", paquet[position:position + 10])
        position += 10
        donnees = paquet[position:position + taille]
        if type_r == 16 == code:
            textes, i = [], 0
            while i < len(donnees):
                textes.append(donnees[i + 1:i + 1 + donnees[i]].decode("utf-8", "replace"))
                i += 1 + donnees[i]
            resultats.append("".join(textes))
        elif type_r == 2 == code:
            resultats.append(_lire_nom(paquet, position)[0])
        position += taille
    return resultats


def serveurs_de_la_zone(zone, resolveurs=RESOLVEURS_PUBLICS):
    for hote, port in resolveurs:
        try:
            noms = requete_dns(zone, "NS", hote, port)
        except OSError:
            continue
        adresses = []
        for ns in noms:
            try:
                adresses += sorted({a[4][0] for a in socket.getaddrinfo(ns, 53, socket.AF_INET, socket.SOCK_DGRAM)})
            except OSError:
                pass
        if adresses:
            return [(a, 53) for a in adresses]
    return []


def attendre_publication(fqdn, valeur, serveurs, *, delai=300, pause=5.0, dormir=time.sleep, horloge=time.monotonic):
    """True dès que chaque serveur publie la valeur ; False après le délai."""
    fin = horloge() + delai
    while True:
        vus = 0
        for hote, port in serveurs:
            try:
                if valeur in requete_dns(fqdn, "TXT", hote, port, recursion=False):
                    vus += 1
            except OSError:
                pass
        if serveurs and vus == len(serveurs):
            return True
        if horloge() >= fin:
            return False
        dormir(pause)


# ---------------------------------------------------------------------------
# ACME (RFC 8555), clé de compte ECDSA P-256
# ---------------------------------------------------------------------------

def _b64(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).rstrip(b"=").decode("ascii")


class ClientAcme:
    def __init__(self, repertoire, cle_compte, *, ca=None, dormir=time.sleep):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.ca, self.cle, self.dormir = ca, cle_compte, dormir
        nombres = cle_compte.public_key().public_numbers()
        self.jwk = {"crv": "P-256", "kty": "EC",
                    "x": _b64(nombres.x.to_bytes(32, "big")), "y": _b64(nombres.y.to_bytes(32, "big"))}
        assert isinstance(cle_compte, ec.EllipticCurvePrivateKey)
        statut, _, corps = self._get(repertoire)
        if statut != 200:
            raise ErreurCertificat(f"Service de certificats injoignable ({statut}).")
        self.annuaire = json.loads(corps)
        self.nonce = None
        self.kid = None

    def _get(self, url):
        try:
            return _https(url, ca=self.ca)
        except (OSError, ssl.SSLError) as exc:
            raise ErreurCertificat(f"Service de certificats injoignable : {exc}") from None

    def empreinte(self) -> str:
        canonique = json.dumps(self.jwk, sort_keys=True, separators=(",", ":")).encode()
        return _b64(hashlib.sha256(canonique).digest())

    def _nouveau_nonce(self):
        try:
            _, entetes, _ = _https(self.annuaire["newNonce"], methode="HEAD", ca=self.ca)
        except (OSError, ssl.SSLError) as exc:
            raise ErreurCertificat(f"Service de certificats injoignable : {exc}") from None
        return {k.lower(): v for k, v in entetes.items()}.get("replay-nonce")

    def _signer(self, url, charge):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        entete = {"alg": "ES256", "nonce": self.nonce or self._nouveau_nonce(), "url": url}
        self.nonce = None  # un nonce ne sert qu'une fois
        entete.update({"kid": self.kid} if self.kid else {"jwk": self.jwk})
        protege = _b64(json.dumps(entete).encode())
        contenu = "" if charge is None else _b64(json.dumps(charge).encode())
        r, s = decode_dss_signature(self.cle.sign(f"{protege}.{contenu}".encode(), ec.ECDSA(hashes.SHA256())))
        signature = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return json.dumps({"protected": protege, "payload": contenu, "signature": signature}).encode()

    def post(self, url, charge, *, accepter=None):
        """POST signé (charge None = « POST-as-GET »), nonce rejoué si refusé."""
        for _ in range(6):
            entetes = {"Content-Type": "application/jose+json"}
            if accepter:
                entetes["Accept"] = accepter
            try:
                statut, reponse, corps = _https(url, methode="POST", donnees=self._signer(url, charge), entetes=entetes, ca=self.ca)
            except (OSError, ssl.SSLError) as exc:
                raise ErreurCertificat(f"Service de certificats injoignable : {exc}") from None
            reponse = {k.lower(): v for k, v in reponse.items()}
            self.nonce = reponse.get("replay-nonce")
            if statut >= 400:
                try:
                    probleme = json.loads(corps)
                except ValueError:
                    probleme = {}
                if probleme.get("type") == "urn:ietf:params:acme:error:badNonce":
                    continue
                raise ErreurCertificat(f"Let's Encrypt refuse : {probleme.get('detail') or statut}")
            return statut, reponse, corps
        raise ErreurCertificat("Let's Encrypt refuse les jetons de requête à répétition.")

    def compte(self, url_connue=None):
        if url_connue:
            self.kid = url_connue
            return url_connue
        _, entetes, _ = self.post(self.annuaire["newAccount"], {"termsOfServiceAgreed": True})
        self.kid = entetes["location"]
        return self.kid

    def attendre(self, url, *, fini=("valid",), echec=("invalid",), delai=180):
        debut = time.monotonic()
        while True:
            _, entetes, corps = self.post(url, None)
            objet = json.loads(corps)
            if objet.get("status") in fini:
                return objet
            if objet.get("status") in echec:
                return objet
            if time.monotonic() - debut > delai:
                raise ErreurCertificat("Let's Encrypt n'a pas répondu à temps ; réessayez plus tard.")
            try:
                pause = min(max(float(entetes.get("retry-after", 2)), 1.0), 10.0)
            except ValueError:
                pause = 2.0
            self.dormir(pause)


def _cle_compte(root, repertoire):
    """Clé du compte ACME, dans private/acme : dossier que l'outil Windows crée
    réservé aux administrateurs et à SYSTEM (private/ reste lisible par le
    service web ; cette clé permet de commander un certificat pour le nom
    tant que l'autorisation de Let's Encrypt reste valable)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    chemin = Path(root) / "private" / "acme" / "compte.pem"
    fiche = Path(root) / "private" / "acme" / "comptes.json"
    if chemin.exists():
        cle = serialization.load_pem_private_key(chemin.read_bytes(), None)
    else:
        cle = ec.generate_private_key(ec.SECP256R1())
        _ecrire_atomique(chemin, cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
    try:
        comptes = json.loads(fiche.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        comptes = {}
    return cle, comptes.get(repertoire), fiche, comptes


def obtenir(c, root, *, cpanel=None, serveurs_dns=None, dormir=time.sleep, delai_publication=300):
    """Demande un certificat pour le nom configuré et l'écrit dans https/public.
    Rend l'expiration du certificat obtenu."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    r = reglages(c)
    if r is None:
        raise ErreurCertificat("Aucun nom public configuré.")
    nom = nom_public(c)
    import re
    if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", nom):
        raise ErreurCertificat(f"« {nom} » n'est pas un nom de domaine public valide.")
    cpanel = cpanel or CPanel(r.get("cpanel_hote"), r.get("cpanel_utilisateur"), c.get("certificat_public_jeton"),
                              ca=r.get("cpanel_ca"))
    zone = cpanel.zone(nom)
    if serveurs_dns is None and r.get("dns_serveurs"):
        serveurs_dns = [(str(h), int(p)) for h, p in r["dns_serveurs"]]
    repertoire = r.get("acme_repertoire") or LETSENCRYPT
    cle, url_compte, fiche, comptes = _cle_compte(root, repertoire)
    acme = ClientAcme(repertoire, cle, ca=r.get("acme_ca"), dormir=dormir)
    if not url_compte:
        comptes[repertoire] = acme.compte()
        _ecrire_atomique(fiche, json.dumps(comptes, indent=1).encode("utf-8"))
    else:
        acme.compte(url_compte)
    _, entetes, corps = acme.post(acme.annuaire["newOrder"], {"identifiers": [{"type": "dns", "value": nom}]})
    commande_url, commande = entetes["location"], json.loads(corps)
    for url_autorisation in commande["authorizations"]:
        _, _, corps = acme.post(url_autorisation, None)
        autorisation = json.loads(corps)
        if autorisation.get("status") == "valid":
            continue
        defi = next((d for d in autorisation.get("challenges", []) if d.get("type") == "dns-01"), None)
        if defi is None:
            raise ErreurCertificat("Let's Encrypt ne propose pas la validation par DNS.")
        cle_autorisation = f"{defi['token']}.{acme.empreinte()}"
        valeur = _b64(hashlib.sha256(cle_autorisation.encode()).digest())
        fqdn = "_acme-challenge." + autorisation["identifier"]["value"].lower()
        cpanel.ajouter_txt(zone, fqdn, valeur)
        try:
            serveurs = serveurs_dns if serveurs_dns is not None else serveurs_de_la_zone(zone)
            if serveurs:
                if not attendre_publication(fqdn, valeur, serveurs, delai=delai_publication, dormir=dormir):
                    raise ErreurCertificat("L'enregistrement DNS n'est pas publié par les serveurs d'O2Switch ; réessayez dans quelques minutes.")
            else:
                dormir(90)  # serveurs de la zone introuvables : délai prudent
            acme.post(defi["url"], {})
            resultat = acme.attendre(url_autorisation, fini=("valid",), echec=("invalid", "revoked", "expired", "deactivated"))
            if resultat.get("status") != "valid":
                detail = next((d.get("error", {}).get("detail") for d in resultat.get("challenges", []) if d.get("error")), None)
                raise ErreurCertificat(f"Let's Encrypt n'a pas validé le nom : {detail or resultat.get('status')}")
        finally:
            try:
                cpanel.retirer_txt(zone, fqdn, valeur)
            except ErreurCertificat:
                pass
    cle_site = ec.generate_private_key(ec.SECP256R1())
    demande = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nom)]))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(nom)]), critical=False)
               .sign(cle_site, hashes.SHA256()))
    commande = acme.attendre(commande_url, fini=("ready", "valid"), echec=("invalid",))
    if commande.get("status") == "ready":
        acme.post(commande["finalize"], {"csr": _b64(demande.public_bytes(serialization.Encoding.DER))})
        commande = acme.attendre(commande_url, fini=("valid",), echec=("invalid",))
    if commande.get("status") != "valid":
        raise ErreurCertificat("Let's Encrypt a refusé la demande de certificat.")
    _, _, chaine = acme.post(commande["certificate"], None, accepter="application/pem-certificate-chain")
    feuille = x509.load_pem_x509_certificates(chaine)[0]
    publique = serialization.PublicFormat.SubjectPublicKeyInfo
    if feuille.public_key().public_bytes(serialization.Encoding.DER, publique) != cle_site.public_key().public_bytes(serialization.Encoding.DER, publique):
        raise ErreurCertificat("Certificat reçu incohérent ; rien n'a été remplacé.")
    certificat, cle_fichier = fichiers(root)
    # Clé d'abord, puis certificat : un lecteur qui voit le nouveau certificat
    # trouve déjà la bonne clé (certificat_valide vérifie la paire).
    _ecrire_atomique(cle_fichier, cle_site.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    _ecrire_atomique(certificat, chaine)
    return feuille.not_valid_after_utc


def executer(c, *, forcer=False, **options) -> int:
    """0 : certificat en place et valable ; NOUVEAU : certificat écrit ;
    lève ErreurCertificat sinon (le certificat précédent reste en place)."""
    root = Path(c["data_root"])
    if reglages(c) is None:
        return 0
    maintenant = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    if not forcer and not a_renouveler(c, root):
        valide = certificat_valide(c, root)
        _noter_etat(root, nom=nom_public(c), expire_le=valide[2].isoformat(), verifie_le=maintenant)
        return 0
    try:
        expiration = obtenir(c, root, **options)
    except ErreurCertificat as exc:
        valide = certificat_valide(c, root)
        _noter_etat(root, nom=nom_public(c), dernier_essai=maintenant, erreur=str(exc),
                    expire_le=valide[2].isoformat() if valide else None)
        raise
    _noter_etat(root, nom=nom_public(c), expire_le=expiration.isoformat(), obtenu_le=maintenant,
                dernier_essai=maintenant, erreur=None)
    return NOUVEAU
