"""Connecter un appareil : quelle adresse ouvrir, et comment éviter l'alerte.

Un centre sans informaticien bute sur deux pièges quand il branche une
tablette ou le poste d'un collègue :

- **la mauvaise adresse** : sur une installation « ce poste uniquement »,
  l'application répond à 127.0.0.1, que les autres appareils ne joignent
  pas ; un QR code vers cette adresse ne mène nulle part ;
- **l'alerte de sécurité** (« Votre connexion n'est pas privée ») : le
  serveur Windows signe ses pages avec le certificat du centre, que les
  appareils ne connaissent pas. L'installateur le dépose dans
  ``public/Certificat-du-centre.cer`` et la documentation disait « la DSI le
  déploie » — sans DSI, personne ne savait quoi en faire.

La tablette d'émargement n'est pas concernée : le kiosque a son adresse en
http:// sur le réseau local, sans certificat. Avec un certificat reconnu
(nom public, Let's Encrypt), les collègues n'ont rien à installer non plus.

Le certificat du centre est public (aucun secret : sa clé privée reste au
service HTTPS). Une installation ancienne a une autorité sans contrainte de
noms : elle pourrait signer pour n'importe quel site si elle était volée,
on ne la conseille donc que sur les appareils du centre.
"""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

NOM_FICHIER = "Certificat-du-centre.cer"
#: Un certificat pèse quelques kilo-octets ; au-delà, ce n'en est pas un.
TAILLE_MAX = 64 * 1024


@dataclass(frozen=True)
class CertificatCentre:
    contenu: bytes  # format DER, tel que l'installateur Windows l'exporte
    nom: str
    #: Empreinte SHA-1 groupée comme dans la fenêtre de confirmation de
    #: Windows (« Empreinte numérique (sha1) ») : de quoi vérifier que le
    #: fichier installé est bien celui du serveur.
    empreinte_windows: str
    expire_le: datetime.date
    #: Autorité limitée aux noms et adresses du centre (installations récentes).
    bride: bool


def _dossier_donnees() -> Path | None:
    racine = os.environ.get("APP_DATA_DIR")
    return Path(racine) if racine else None


def certificat_du_centre() -> CertificatCentre | None:
    """Le certificat que les appareils doivent connaître, ou None (absent, illisible)."""
    from cryptography import x509

    dossier = _dossier_donnees()
    if dossier is None:
        return None
    chemin = dossier / "public" / NOM_FICHIER
    try:
        if not chemin.is_file() or chemin.stat().st_size > TAILLE_MAX:
            return None
        brut = chemin.read_bytes()
    except OSError:
        return None
    try:
        cert = x509.load_der_x509_certificate(brut)
    except ValueError:
        try:
            cert = x509.load_pem_x509_certificate(brut)
        except ValueError:
            return None
    from cryptography.hazmat.primitives.serialization import Encoding

    der = cert.public_bytes(Encoding.DER)
    sha1 = hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()
    try:
        cert.extensions.get_extension_for_class(x509.NameConstraints)
        bride = True
    except x509.ExtensionNotFound:
        bride = False
    noms = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return CertificatCentre(
        contenu=der,
        nom=str(noms[0].value) if noms else "Mon Centre Social",
        empreinte_windows=" ".join(sha1[i:i + 8] for i in range(0, len(sha1), 8)),
        expire_le=cert.not_valid_after_utc.date(),
        bride=bride,
    )


def nom_public() -> str:
    """Nom à certificat reconnu (Let's Encrypt) configuré sur le serveur, sinon ""."""
    from app.services.registre_externe import dossier_runtime

    dossier = dossier_runtime()
    if dossier is None:
        return ""
    try:
        etat = json.loads((dossier / "certificat-public.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(etat.get("nom") or "").strip() if isinstance(etat, dict) else ""


def adresse_locale_seulement(adresse: str) -> bool:
    """L'adresse ne marche que sur le serveur lui-même (localhost, 127.0.0.1)."""
    hote = (urlsplit(adresse).hostname or "").lower()
    if hote == "localhost" or hote.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hote).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Connexion:
    adresse_kiosque: str
    adresse_installation: str
    kiosque_local_seulement: bool
    application_locale_seulement: bool
    hors_les_murs: bool
    https: bool
    nom_public: str
    certificat: CertificatCentre | None

    @property
    def guide_certificat(self) -> bool:
        """Faut-il expliquer comment faire confiance au certificat du centre ?"""
        return self.https and not self.nom_public and self.certificat is not None


def etat() -> Connexion:
    """Ce que la page « Connecter un appareil » affiche (dans une requête)."""
    from flask import current_app, url_for

    from app.pwa.routes import adresse_installation
    from app.services.public_urls import kiosk_public_base_url, public_base_url

    kiosque = f"{kiosk_public_base_url()}{url_for('kiosk.kiosk_home')}"
    installation = adresse_installation()
    nom = nom_public()
    # Le nom public ne vaut que s'il est l'adresse donnée aux collègues.
    if nom and (urlsplit(public_base_url()).hostname or "").lower() != nom.lower():
        nom = ""
    return Connexion(
        adresse_kiosque=kiosque,
        adresse_installation=installation,
        kiosque_local_seulement=adresse_locale_seulement(kiosque),
        application_locale_seulement=adresse_locale_seulement(installation),
        hors_les_murs=bool((current_app.config.get("KIOSK_PUBLIC_HOST") or "").strip()),
        https=installation.startswith("https://"),
        nom_public=nom,
        certificat=certificat_du_centre(),
    )
