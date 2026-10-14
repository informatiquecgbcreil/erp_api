"""Chiffrement des copies cloud : ce qui part chez l'hébergeur est illisible
sans la phrase secrète, et se rouvre à l'identique avec elle.

Ces tests n'ont besoin ni de base ni d'application : le format doit pouvoir
se relire sur un serveur réinstallé à neuf, avec la seule phrase secrète.
"""
import os

import pytest

from app.services import chiffrement_sauvegarde as chiffrement

PHRASE = "le chat de la voisine mange des radis"


@pytest.fixture(scope="module")
def trousseau():
    return chiffrement.creer_trousseau(PHRASE)


def _fichier(tmp_path, contenu: bytes):
    source = tmp_path / "lot.sql"
    source.write_bytes(contenu)
    return source


def test_aller_retour_a_l_identique_sur_plusieurs_morceaux(tmp_path, trousseau):
    # 3,5 Mio : plusieurs morceaux de 1 Mio, et un dernier incomplet.
    contenu = os.urandom(3 * 1024 * 1024 + 512 * 1024)
    source = _fichier(tmp_path, contenu)
    chiffre = tmp_path / "lot.sql.chiffre"
    empreinte = chiffrement.chiffrer_fichier(source, chiffre, trousseau)
    import hashlib

    assert empreinte == hashlib.sha256(chiffre.read_bytes()).hexdigest()
    rendu = tmp_path / "rendu.sql"
    chiffrement.dechiffrer_fichier(chiffre, rendu, PHRASE)
    assert rendu.read_bytes() == contenu


def test_rien_de_lisible_dans_la_copie(tmp_path, trousseau):
    texte = "INSERT INTO participant VALUES ('Maëlys', 'Zoubirou', 'maelys@exemple.fr');".encode() * 200
    chiffre = tmp_path / "lot.sql.chiffre"
    chiffrement.chiffrer_fichier(_fichier(tmp_path, texte), chiffre, trousseau)
    brut = chiffre.read_bytes()
    for morceau in (b"Zoubirou", "Maëlys".encode(), b"maelys@exemple.fr", b"INSERT INTO"):
        assert morceau not in brut


def test_fichier_vide(tmp_path, trousseau):
    chiffre = tmp_path / "vide.chiffre"
    chiffrement.chiffrer_fichier(_fichier(tmp_path, b""), chiffre, trousseau)
    chiffrement.dechiffrer_fichier(chiffre, tmp_path / "rendu", PHRASE)
    assert (tmp_path / "rendu").read_bytes() == b""


def test_mauvaise_phrase_ne_laisse_rien(tmp_path, trousseau):
    chiffre = tmp_path / "lot.chiffre"
    chiffrement.chiffrer_fichier(_fichier(tmp_path, b"donnees"), chiffre, trousseau)
    rendu = tmp_path / "rendu"
    with pytest.raises(chiffrement.PhraseIncorrecte):
        chiffrement.dechiffrer_fichier(chiffre, rendu, "ce n'est pas la bonne phrase")
    assert list(tmp_path.glob("rendu*")) == []
    assert chiffrement.phrase_ouvre(chiffre, PHRASE)
    assert not chiffrement.phrase_ouvre(chiffre, PHRASE + " ")


@pytest.mark.parametrize("abimer", [
    lambda b: b[:-1] + bytes([b[-1] ^ 1]),                 # étiquette
    lambda b: b[:200] + bytes([b[200] ^ 1]) + b[201:],     # données
    lambda b: b[:-40],                                     # tronqué
    lambda b: b[:100] + bytes([b[100] ^ 1]) + b[101:],     # clé éphémère (en-tête)
])
def test_copie_alteree_refusee_sans_rien_laisser(tmp_path, trousseau, abimer):
    chiffre = tmp_path / "lot.chiffre"
    chiffrement.chiffrer_fichier(_fichier(tmp_path, os.urandom(5000)), chiffre, trousseau)
    chiffre.write_bytes(abimer(chiffre.read_bytes()))
    with pytest.raises(chiffrement.CopieIllisible):
        chiffrement.dechiffrer_fichier(chiffre, tmp_path / "rendu", PHRASE)
    assert list(tmp_path.glob("rendu*")) == []


def test_un_fichier_quelconque_n_est_pas_une_copie(tmp_path):
    with pytest.raises(chiffrement.CopieIllisible, match="pas une copie"):
        chiffrement.dechiffrer_fichier(_fichier(tmp_path, b"PK\x03\x04 un zip"), tmp_path / "rendu", PHRASE)


def test_la_cle_privee_n_est_gardee_que_protegee(trousseau):
    """Ni la phrase ni la clé privée en clair ne se trouvent dans ce que garde le serveur."""
    privee = chiffrement.ouvrir_cle_privee(PHRASE, trousseau.cle_privee_protegee)
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    brute = privee.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    garde = trousseau.cle_publique + trousseau.cle_privee_protegee
    assert brute not in garde and PHRASE.encode() not in garde
    assert chiffrement.phrase_correcte(PHRASE, trousseau.cle_privee_protegee)
    assert not chiffrement.phrase_correcte("autre phrase bien longue", trousseau.cle_privee_protegee)


def test_phrase_trop_courte_refusee():
    with pytest.raises(ValueError, match="au moins"):
        chiffrement.creer_trousseau("trop courte")


def test_deux_trousseaux_ne_s_ouvrent_pas_l_un_l_autre(tmp_path, trousseau):
    autre = chiffrement.creer_trousseau("une toute autre phrase secrète")
    chiffre = tmp_path / "lot.chiffre"
    chiffrement.chiffrer_fichier(_fichier(tmp_path, b"donnees"), chiffre, autre)
    with pytest.raises(chiffrement.PhraseIncorrecte):
        chiffrement.dechiffrer_fichier(chiffre, tmp_path / "rendu", PHRASE)
