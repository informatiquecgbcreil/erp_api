"""Fichiers de registre hors base : verrou, lecture stricte, écriture atomique.

Deux registres vivent dans le dossier ``runtime`` des données de
l'installation (APP_DATA_DIR), hors des sauvegardes ordinaires :

- les numéros de reçus, factures et avoirs déjà émis
  (``financial_sequence``) ;
- les effacements RGPD confirmés (``registre_effacements``).

Ce module ne connaît que le FICHIER. Les règles métier restent chez chacun
(un numéro réservé puis abandonné reste consommé ; un effacement annulé ne
doit jamais devenir confirmé) : elles ne sont volontairement pas mutualisées.

Garanties apportées ici :

- **Verrou exclusif inter-processus et inter-fils** autour de toute la
  séquence lecture → modification → écriture (fichier ``<nom>.lock`` voisin,
  ``fcntl.flock`` sous Linux, ``msvcrt.locking`` sous Windows). Délai
  borné : au-delà, ``RegistreVerrouille`` est levée, jamais d'attente sans fin.
  Le verrou est tenu par le système : un processus tué le libère.
- **Écriture atomique** : fichier temporaire propre à l'écrivain (nom unique),
  ``fsync``, puis remplacement. Un arrêt brutal laisse soit l'ancienne
  version, soit la nouvelle, jamais un mélange. Les temporaires laissés par
  un arrêt sont supprimés au passage suivant, sous verrou.
- **Copie précédente** (``<nom>.prec``) conservée à chaque écriture.
- **Lecture stricte** : fichier absent = registre vide (première
  installation) ; fichier présent mais illisible ou inaccessible =
  ``RegistreIllisible``, jamais un registre vide en silence.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

#: Attente maximale du verrou (secondes). Une écriture dure quelques
#: millisecondes : dix secondes signalent un vrai blocage.
DELAI_VERROU = 10.0
#: Sous Windows, un antivirus ou un outil de sauvegarde peut tenir le fichier
#: ouvert un instant : le remplacement est retenté pendant ce délai.
DELAI_REMPLACEMENT = 3.0


class RegistreErreur(RuntimeError):
    """Le registre n'a pas pu être lu ou écrit. Le message s'adresse à
    l'utilisateur ; le détail technique est dans ``detail``."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class RegistreVerrouille(RegistreErreur):
    pass


class RegistreIllisible(RegistreErreur):
    """Fichier présent mais impossible à lire (droits, disque, contenu)."""


class RegistreEndommage(RegistreIllisible):
    """Fichier lisible dont le contenu n'est pas un registre valide."""


def dossier_runtime() -> Path | None:
    racine = os.environ.get("APP_DATA_DIR")
    return Path(racine) / "runtime" if racine else None


_verrous_locaux: dict[str, threading.Lock] = {}
_verrous_locaux_garde = threading.Lock()


def _verrou_local(chemin: Path) -> threading.Lock:
    cle = os.path.normcase(os.path.abspath(chemin))
    with _verrous_locaux_garde:
        return _verrous_locaux.setdefault(cle, threading.Lock())


def _essayer_verrou_systeme(fichier) -> bool:
    if sys.platform == "win32":
        import msvcrt
        fichier.seek(0)
        try:
            msvcrt.locking(fichier.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fichier.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _liberer_verrou_systeme(fichier) -> None:
    if sys.platform == "win32":
        import msvcrt
        fichier.seek(0)
        msvcrt.locking(fichier.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fichier.fileno(), fcntl.LOCK_UN)


@contextmanager
def verrou(chemin: Path, delai: float = DELAI_VERROU):
    """Verrou exclusif sur ``chemin`` (fils du processus ET autres processus)."""
    fin = time.monotonic() + delai
    local = _verrou_local(chemin)
    if not local.acquire(timeout=max(0.0, delai)):
        raise RegistreVerrouille(
            "Le registre est occupé par une autre opération : réessayez dans un instant.",
            f"verrou interne non obtenu en {delai:.0f} s : {chemin.name}")
    try:
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            fichier = open(chemin.with_name(chemin.name + ".lock"), "a+b")
        except OSError as exc:
            raise RegistreErreur(
                "Le registre de l'installation est inaccessible : vérifiez les droits du dossier des données.",
                f"ouverture du verrou impossible : {chemin.name} ({exc.__class__.__name__})") from exc
        try:
            while not _essayer_verrou_systeme(fichier):
                if time.monotonic() >= fin:
                    raise RegistreVerrouille(
                        "Le registre est occupé par une autre opération : réessayez dans un instant.",
                        f"verrou système non obtenu en {delai:.0f} s : {chemin.name}")
                time.sleep(0.02)
            try:
                yield
            finally:
                _liberer_verrou_systeme(fichier)
        finally:
            fichier.close()
    finally:
        local.release()


def lire(chemin: Path) -> dict | None:
    """Contenu du registre ; ``None`` s'il n'existe pas encore.

    Lève ``RegistreIllisible`` si le fichier existe mais ne peut pas être lu
    (droits, disque) ou n'est pas un objet JSON."""
    try:
        brut = chemin.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RegistreIllisible(
            "Le registre de l'installation ne peut pas être lu : vérifiez les droits du dossier des données.",
            f"lecture impossible : {chemin.name} ({exc.__class__.__name__})") from exc
    try:
        donnees = json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RegistreEndommage(
            "Le registre de l'installation est endommagé.",
            f"contenu illisible : {chemin.name} ({exc.__class__.__name__})") from exc
    if not isinstance(donnees, dict):
        raise RegistreEndommage("Le registre de l'installation est endommagé.",
                                f"contenu inattendu : {chemin.name}")
    return donnees


def _fsync_dossier(dossier: Path) -> None:
    if sys.platform == "win32":
        return  # Le remplacement NTFS est journalisé ; pas de fsync de dossier.
    try:
        fd = os.open(dossier, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _remplacer(source: Path, cible: Path) -> None:
    fin = time.monotonic() + DELAI_REMPLACEMENT
    while True:
        try:
            os.replace(source, cible)
            return
        except PermissionError:
            if sys.platform != "win32" or time.monotonic() >= fin:
                raise
            time.sleep(0.05)


def temporaires(chemin: Path) -> list[Path]:
    return sorted(chemin.parent.glob(chemin.name + ".*.tmp"))


def nettoyer_temporaires(chemin: Path) -> int:
    """Supprime les temporaires d'écritures interrompues. À appeler SOUS le
    verrou : aucun autre écrivain ne peut alors être en cours."""
    nombre = 0
    for residu in temporaires(chemin):
        try:
            residu.unlink()
            nombre += 1
        except OSError:
            pass
    return nombre


def ecrire(chemin: Path, donnees: dict) -> None:
    """Écriture atomique et durable (à appeler sous le verrou)."""
    contenu = json.dumps(donnees, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")
    temporaire = chemin.with_name(f"{chemin.name}.{os.getpid()}-{uuid.uuid4().hex[:12]}.tmp")
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with open(temporaire, "wb") as fichier:
            fichier.write(contenu)
            fichier.flush()
            os.fsync(fichier.fileno())
        if chemin.exists():
            # Copie de la version précédente, elle aussi remplacée atomiquement.
            precedent = chemin.with_name(chemin.name + ".prec")
            copie = chemin.with_name(f"{chemin.name}.prec.{uuid.uuid4().hex[:12]}.tmp")
            copie.write_bytes(chemin.read_bytes())
            _remplacer(copie, precedent)
        _remplacer(temporaire, chemin)
        _fsync_dossier(chemin.parent)
    except OSError as exc:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass
        raise RegistreErreur(
            "Le registre de l'installation n'a pas pu être enregistré (disque plein ou droits insuffisants).",
            f"écriture impossible : {chemin.name} ({exc.__class__.__name__})") from exc


def mettre_de_cote(chemin: Path) -> Path | None:
    """Renomme un registre illisible (jamais supprimé : il reste examinable)."""
    if not chemin.exists():
        return None
    horodatage = time.strftime("%Y%m%d-%H%M%S")
    cible = chemin.with_name(f"{chemin.stem}.illisible-{horodatage}-{uuid.uuid4().hex[:6]}{chemin.suffix}")
    _remplacer(chemin, cible)
    return cible


def registres_mis_de_cote() -> list[Path]:
    dossier = dossier_runtime()
    if dossier is None or not dossier.exists():
        return []
    return sorted(dossier.glob("*.illisible-*"))
