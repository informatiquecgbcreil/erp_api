"""Recette Windows éphémère des registres hors base (non distribuée).

Exécutée par la CI avec le Python livré par l'installateur, sur Windows
Server : vérifie que le verrou (``msvcrt.locking``) et l'écriture atomique
du module ``app/services/registre_externe.py`` tiennent entre processus
indépendants, comme entre le service web, la sauvegarde et la maintenance.

    python recette_registres.py <dossier d'installation>
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ECRIVAINS, TOURS = 6, 40


def charger(installation: Path):
    # Même disposition que desktop/runtime.py : l'application livrée est
    # dans « application », une copie de travail à la racine.
    racine = installation / "application" if (installation / "application").is_dir() else installation
    spec = importlib.util.spec_from_file_location(
        "registre_externe", racine / "app" / "services" / "registre_externe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ajouter(reg, chemin: Path, valeurs: dict) -> None:
    """Même séquence que ``financial_sequence.noter_maxima`` : lecture,
    maximum par série, écriture, le tout sous verrou."""
    with reg.verrou(chemin):
        reg.nettoyer_temporaires(chemin)
        actuel = reg.lire(chemin) or {}
        for cle, valeur in valeurs.items():
            actuel[cle] = max(int(actuel.get(cle, 0)), valeur)
        reg.ecrire(chemin, actuel)


def enfant(installation: Path, chemin: Path, mode: str, argument: str) -> int:
    reg = charger(installation)
    if mode == "ecrivain":
        depart = chemin.with_name("depart")
        while not depart.exists():
            time.sleep(0.01)
        for tour in range(1, TOURS + 1):
            ajouter(reg, chemin, {f"serie-{argument}": tour, "commune": tour})
        return 0
    if mode == "verrou":
        try:
            with reg.verrou(chemin, delai=0.3):
                return 0
        except reg.RegistreVerrouille:
            return 3
    if mode == "coupure":
        def coupure(*_args, **_kwargs):
            os._exit(9)
        reg._remplacer = coupure
        ajouter(reg, chemin, {"coupee": 1})
        return 0
    raise ValueError(mode)


def lancer(installation: Path, chemin: Path, mode: str, argument: str = "") -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-B", __file__, str(installation), "--enfant", mode, str(chemin), argument])


def main() -> None:
    installation = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2 and sys.argv[2] == "--enfant":
        raise SystemExit(enfant(installation, Path(sys.argv[4]), sys.argv[3], sys.argv[5] if len(sys.argv) > 5 else ""))
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Recette réservée à la CI Windows éphémère.")
    reg = charger(installation)
    with tempfile.TemporaryDirectory(prefix="recette-registres-", dir=os.environ.get("RUNNER_TEMP")) as dossier:
        chemin = Path(dossier) / "numeros-emis.json"

        # 1. Processus concurrents : aucune série perdue, aucun maximum qui recule.
        processus = [lancer(installation, chemin, "ecrivain", str(i)) for i in range(ECRIVAINS)]
        chemin.with_name("depart").write_text("1", encoding="ascii")
        for p in processus:
            assert p.wait(300) == 0, "un écrivain a échoué"
        attendu = {f"serie-{i}": TOURS for i in range(ECRIVAINS)}
        attendu["commune"] = TOURS
        assert json.loads(chemin.read_text(encoding="utf-8")) == attendu, chemin.read_text(encoding="utf-8")
        print("REGISTRES_PROCESSUS_CONCURRENTS_WINDOWS_OK")

        # 2. Verrou tenu par un autre processus : attente bornée, puis refus.
        with reg.verrou(chemin):
            assert lancer(installation, chemin, "verrou").wait(120) == 3, "verrou ignoré par un autre processus"
        assert lancer(installation, chemin, "verrou").wait(120) == 0, "verrou non libéré"
        print("REGISTRES_VERROU_BORNE_WINDOWS_OK")

        # 3. Arrêt brutal pendant l'écriture : ancienne version intacte, résidu
        #    nettoyé au passage suivant.
        avant = chemin.read_bytes()
        assert lancer(installation, chemin, "coupure").wait(120) == 9
        assert chemin.read_bytes() == avant
        assert reg.temporaires(chemin), "aucun résidu d'écriture interrompue"
        ajouter(reg, chemin, {"apres-coupure": 1})
        assert not reg.temporaires(chemin)
        print("REGISTRES_ARRET_BRUTAL_WINDOWS_OK")

        # 4. Fichier tenu ouvert un instant (antivirus, outil de sauvegarde) :
        #    le remplacement est retenté au lieu d'échouer.
        ouvert = open(chemin, "rb")
        threading.Timer(0.5, ouvert.close).start()
        ajouter(reg, chemin, {"apres-antivirus": 1})
        assert json.loads(chemin.read_text(encoding="utf-8"))["apres-antivirus"] == 1
        print("REGISTRES_FICHIER_OCCUPE_WINDOWS_OK")


if __name__ == "__main__":
    main()
