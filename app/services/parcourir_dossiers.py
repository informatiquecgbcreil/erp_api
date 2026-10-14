"""Parcourir les dossiers du serveur, pour choisir une destination de sauvegarde.

Le navigateur ne peut pas ouvrir l'explorateur du serveur : il tourne sur le
poste de la personne, le dossier est sur le serveur. Cette petite liste,
réservée aux administrateurs, montre les disques et les dossiers tels que les
voit le service de l'application. Un lecteur réseau monté par un utilisateur
(Z:) n'y figure donc pas, et c'est voulu : la sauvegarde de la nuit ne le
verrait pas non plus. On y tape alors l'adresse \\\\NAS\\partage.

Seuls les noms de dossiers sont listés, jamais les fichiers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: Dossiers système sans intérêt pour une sauvegarde.
_IGNORES = {"$recycle.bin", "system volume information", "recovery", "config.msi", "lost+found", "proc", "sys",
            "dev", "run"}
MAX_DOSSIERS = 500

#: GetDriveTypeW (Windows) : https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getdrivetypew
_TYPES_WINDOWS = {2: "amovible", 3: "fixe", 4: "réseau", 5: "cd"}


def _type_lecteur(racine: str) -> str:
    try:
        import ctypes

        return _TYPES_WINDOWS.get(ctypes.windll.kernel32.GetDriveTypeW(racine), "autre")
    except (AttributeError, OSError):
        return "autre"


def racines() -> list[dict]:
    """Disques (Windows) ou points de montage usuels (Linux, macOS)."""
    if sys.platform == "win32":
        lecteurs = os.listdrives() if hasattr(os, "listdrives") else [f"{l}:\\" for l in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                                                                     if os.path.exists(f"{l}:\\")]
        return [{"chemin": l, "type": _type_lecteur(l)} for l in lecteurs if _type_lecteur(l) != "cd"]
    vus = [{"chemin": "/", "type": "fixe"}]
    for parent in ("/media", "/mnt", "/Volumes"):
        try:
            for entree in sorted(os.scandir(parent), key=lambda e: e.name.lower()):
                if entree.is_dir():
                    vus.append({"chemin": entree.path, "type": "amovible"})
        except OSError:
            continue
    return vus


def sous_dossiers(chemin: str) -> dict:
    """``{chemin, parent, dossiers, erreur}`` pour un dossier du serveur."""
    if chemin.startswith(("\\\\", "//")):
        # Lister \\machine\partage oblige le serveur à s'authentifier auprès de
        # cette machine : un lien piégé (cette lecture n'a pas de jeton CSRF)
        # ferait fuiter l'empreinte de son mot de passe. Une adresse réseau se
        # tape et se teste par « Enregistrer et tester l'écriture » (protégé).
        return {"chemin": chemin, "parent": None, "dossiers": [],
                "erreur": "une adresse réseau ne se parcourt pas : tapez-la, puis « Enregistrer et tester l'écriture »"}
    dossier = Path(chemin)
    if not dossier.is_absolute():
        return {"chemin": chemin, "parent": None, "dossiers": [], "erreur": "indiquez un chemin complet"}
    parent = str(dossier.parent) if dossier.parent != dossier else None
    try:
        entrees = sorted(
            (e.name for e in os.scandir(dossier)
             if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "$"))
             and e.name.lower() not in _IGNORES),
            key=str.lower)
    except OSError as exc:
        return {"chemin": str(dossier), "parent": parent, "dossiers": [],
                "erreur": f"dossier illisible par l'application ({exc.strerror or exc})"}
    return {"chemin": str(dossier), "parent": parent, "dossiers": entrees[:MAX_DOSSIERS], "erreur": None}
