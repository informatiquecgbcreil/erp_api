"""Chiffrement des copies de sauvegarde envoyées vers un dossier cloud.

Une sauvegarde contient les fiches des habitants. Déposée en clair dans
OneDrive ou Google Drive, elle serait lisible par l'hébergeur ou par
quiconque prend le contrôle du compte cloud (décision du centre : la copie
cloud est toujours chiffrée).

La phrase secrète n'est jamais enregistrée :
- à la mise en place, la direction choisit une phrase secrète. Le serveur
  fabrique une paire de clés (X25519) : la clé publique, qui sait seulement
  chiffrer, est gardée en base ; la clé privée n'est gardée que protégée par
  la phrase secrète (scrypt puis AES-GCM) ;
- chaque nuit, la sauvegarde chiffre ses copies avec la clé publique, sans
  personne pour taper la phrase ;
- chaque fichier chiffré emporte la clé privée protégée : la phrase secrète
  suffit pour le rouvrir, même sur un serveur réinstallé à neuf.
Qui prend le serveur trouve la clé publique et la clé privée protégée : sans
la phrase, il ne lit pas les copies cloud.

Format d'un fichier « .chiffre » :
  MAGIE (12 o) | taille de la clé privée protégée (2 o) | clé privée protégée
  | clé publique éphémère (32 o) | nonce (12 o) | données chiffrées
  | étiquette GCM (16 o)
L'en-tête entier est authentifié : modifié, il rend le fichier illisible.
"""
from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

EXTENSION = ".chiffre"
MAGIE = b"MCS-CHIFFRE1"
LONGUEUR_MIN_PHRASE = 16
#: scrypt : 64 Mio de mémoire par essai, ce qui rend coûteuse la recherche
#: d'une phrase par force brute, sans gêner un serveur de centre social.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**16, 8, 1
_VERSION_CLE = 1
_AAD_CLE = b"MCS-cle-privee-1"
_TAILLE_CLE_PROTEGEE = 1 + 16 + 12 + 32 + 16
_MORCEAU = 1024 * 1024
_RAW = Encoding.Raw


class PhraseIncorrecte(ValueError):
    """La phrase secrète ne correspond pas à cette copie."""


class CopieIllisible(ValueError):
    """Fichier tronqué, altéré, ou qui n'est pas une copie chiffrée."""


@dataclass(frozen=True)
class Trousseau:
    cle_publique: bytes
    cle_privee_protegee: bytes


def _cle_de_phrase(phrase: str, sel: bytes) -> bytes:
    return Scrypt(salt=sel, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(phrase.encode("utf-8"))


def creer_trousseau(phrase: str) -> Trousseau:
    if len(phrase) < LONGUEUR_MIN_PHRASE:
        raise ValueError(f"La phrase secrète doit faire au moins {LONGUEUR_MIN_PHRASE} caractères.")
    privee = X25519PrivateKey.generate()
    brute = privee.private_bytes(_RAW, PrivateFormat.Raw, NoEncryption())
    sel, nonce = os.urandom(16), os.urandom(12)
    protegee = bytes([_VERSION_CLE]) + sel + nonce + AESGCM(_cle_de_phrase(phrase, sel)).encrypt(nonce, brute, _AAD_CLE)
    return Trousseau(privee.public_key().public_bytes(_RAW, PublicFormat.Raw), protegee)


def ouvrir_cle_privee(phrase: str, protegee: bytes) -> X25519PrivateKey:
    if len(protegee) != _TAILLE_CLE_PROTEGEE or protegee[0] != _VERSION_CLE:
        raise CopieIllisible("la clé protégée est illisible")
    sel, nonce, chiffre = protegee[1:17], protegee[17:29], protegee[29:]
    try:
        brute = AESGCM(_cle_de_phrase(phrase, sel)).decrypt(nonce, chiffre, _AAD_CLE)
    except InvalidTag:
        raise PhraseIncorrecte("phrase secrète incorrecte") from None
    return X25519PrivateKey.from_private_bytes(brute)


def phrase_correcte(phrase: str, protegee: bytes) -> bool:
    try:
        ouvrir_cle_privee(phrase, protegee)
    except PhraseIncorrecte:
        return False
    return True


def _cle_fichier(secret: bytes, ephemere: bytes, destinataire: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"MCS-sauvegarde-1" + ephemere + destinataire).derive(secret)


def chiffrer_fichier(source: Path, cible: Path, trousseau: Trousseau) -> str:
    """Chiffre ``source`` dans ``cible`` ; renvoie l'empreinte SHA-256 de ``cible``."""
    destinataire = X25519PublicKey.from_public_bytes(trousseau.cle_publique)
    ephemere = X25519PrivateKey.generate()
    ephemere_pub = ephemere.public_key().public_bytes(_RAW, PublicFormat.Raw)
    cle = _cle_fichier(ephemere.exchange(destinataire), ephemere_pub, trousseau.cle_publique)
    nonce = os.urandom(12)
    entete = (MAGIE + struct.pack(">H", len(trousseau.cle_privee_protegee)) + trousseau.cle_privee_protegee
              + ephemere_pub + nonce)
    chiffreur = Cipher(algorithms.AES(cle), modes.GCM(nonce)).encryptor()
    chiffreur.authenticate_additional_data(entete)
    empreinte = hashlib.sha256()
    with source.open("rb") as entree, cible.open("wb") as sortie:
        def ecrire(octets: bytes) -> None:
            sortie.write(octets)
            empreinte.update(octets)

        ecrire(entete)
        while morceau := entree.read(_MORCEAU):
            ecrire(chiffreur.update(morceau))
        ecrire(chiffreur.finalize())
        ecrire(chiffreur.tag)
    return empreinte.hexdigest()


def _lire_exactement(entree, taille: int) -> bytes:
    octets = entree.read(taille)
    if len(octets) != taille:
        raise CopieIllisible("copie tronquée")
    return octets


def _lire_entete(entree) -> tuple[bytes, bytes, bytes, bytes]:
    if entree.read(len(MAGIE)) != MAGIE:
        raise CopieIllisible("ce fichier n'est pas une copie chiffrée de l'application")
    brut_taille = _lire_exactement(entree, 2)
    protegee = _lire_exactement(entree, struct.unpack(">H", brut_taille)[0])
    ephemere_pub = _lire_exactement(entree, 32)
    nonce = _lire_exactement(entree, 12)
    return MAGIE + brut_taille + protegee + ephemere_pub + nonce, protegee, ephemere_pub, nonce


def phrase_ouvre(source: Path, phrase: str) -> bool:
    """La phrase secrète ouvre-t-elle ce fichier chiffré ? (lit seulement l'en-tête)"""
    with source.open("rb") as entree:
        protegee = _lire_entete(entree)[1]
    return phrase_correcte(phrase, protegee)


def dechiffrer_fichier(source: Path, cible: Path, phrase: str) -> None:
    """Déchiffre ``source`` dans ``cible``. Rien n'est laissé à ``cible`` en cas d'échec.

    Lève ``PhraseIncorrecte`` ou ``CopieIllisible``.
    """
    taille = source.stat().st_size
    with source.open("rb") as entree:
        entete, protegee, ephemere_pub, nonce = _lire_entete(entree)
        if taille < len(entete) + 16:
            raise CopieIllisible("copie tronquée")
        privee = ouvrir_cle_privee(phrase, protegee)
        destinataire = privee.public_key().public_bytes(_RAW, PublicFormat.Raw)
        try:
            secret = privee.exchange(X25519PublicKey.from_public_bytes(ephemere_pub))
        except ValueError:
            raise CopieIllisible("en-tête de la copie altéré") from None
        cle = _cle_fichier(secret, ephemere_pub, destinataire)
        entree.seek(taille - 16)
        etiquette = entree.read(16)
        entree.seek(len(entete))
        dechiffreur = Cipher(algorithms.AES(cle), modes.GCM(nonce, etiquette)).decryptor()
        dechiffreur.authenticate_additional_data(entete)
        reste = taille - len(entete) - 16
        provisoire = cible.with_name(cible.name + ".part")
        try:
            with provisoire.open("wb") as sortie:
                while reste > 0:
                    morceau = _lire_exactement(entree, min(_MORCEAU, reste))
                    reste -= len(morceau)
                    sortie.write(dechiffreur.update(morceau))
                try:
                    sortie.write(dechiffreur.finalize())
                except InvalidTag:
                    raise CopieIllisible("copie altérée : elle ne correspond plus à ce qui a été chiffré") from None
            provisoire.replace(cible)
        except BaseException:
            provisoire.unlink(missing_ok=True)
            raise
