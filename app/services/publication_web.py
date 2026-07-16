"""Publication du programme public sur un hébergement web via FTPS.

Pousse la page ``programme.html`` (générée localement, sans aucune donnée
nominative) vers un serveur FTP/cPanel. La connexion est **sortante** et
**chiffrée** (``ftplib.FTP_TLS``, bibliothèque standard) : l'application et la
base de données ne sont jamais exposées sur internet.
"""

from __future__ import annotations

import io
from datetime import datetime
from ftplib import FTP_TLS, all_errors, error_perm
from pathlib import Path

from flask import current_app


def config_publication() -> dict:
    cfg = current_app.config
    return {
        "host": (cfg.get("PROGRAMME_FTP_HOST") or "").strip(),
        "port": int(cfg.get("PROGRAMME_FTP_PORT") or 21),
        "user": (cfg.get("PROGRAMME_FTP_USER") or "").strip(),
        "password": cfg.get("PROGRAMME_FTP_PASSWORD") or "",
        "dir": (cfg.get("PROGRAMME_FTP_DIR") or "").strip(),
        "filename": (cfg.get("PROGRAMME_FTP_FILENAME") or "programme.html").strip(),
        "url_publique": (cfg.get("PROGRAMME_PUBLIC_URL") or "").strip(),
    }


def publication_configuree() -> bool:
    c = config_publication()
    return bool(c["host"] and c["user"] and c["password"])


def _marqueur_path() -> Path:
    return Path(current_app.instance_path) / "derniere_publication.txt"


def derniere_publication() -> datetime | None:
    try:
        txt = _marqueur_path().read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(txt) if txt else None
    except (OSError, ValueError):
        return None


def _marquer_publication() -> None:
    try:
        _marqueur_path().write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8"
        )
    except OSError:
        pass


def _envoyer_ftps(cfg: dict, contenu: bytes) -> None:
    try:
        ftps = FTP_TLS()
        ftps.connect(cfg["host"], cfg["port"], timeout=30)
        ftps.login(cfg["user"], cfg["password"])
        ftps.prot_p()  # chiffre aussi le canal de données
        if cfg["dir"]:
            try:
                ftps.cwd(cfg["dir"])
            except error_perm as exc:
                try:
                    ftps.quit()
                except all_errors:
                    pass
                raise RuntimeError(
                    f"Dossier distant introuvable : « {cfg['dir']} ». "
                    "Vérifiez PROGRAMME_FTP_DIR (par exemple : public_html)."
                ) from exc
        ftps.storbinary(f"STOR {cfg['filename']}", io.BytesIO(contenu))
        ftps.quit()
    except RuntimeError:
        raise
    except all_errors as exc:
        raise RuntimeError(
            f"Échec de la connexion ou de l'envoi FTPS vers {cfg['host']} : {exc}"
        ) from exc


def publier_programme() -> dict:
    """Génère la page programme et la publie via FTPS.

    Lève ``RuntimeError`` (message lisible) si la publication n'est pas
    configurée ou en cas d'échec réseau.
    """
    cfg = config_publication()
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        raise RuntimeError(
            "La publication automatique n'est pas configurée. Renseignez "
            "PROGRAMME_FTP_HOST, PROGRAMME_FTP_USER et PROGRAMME_FTP_PASSWORD "
            "dans la configuration du serveur (.env), puis redémarrez l'application."
        )

    from app.services.programme_public import rendu_html

    contenu = rendu_html().encode("utf-8")
    _envoyer_ftps(cfg, contenu)
    _marquer_publication()
    return {
        "filename": cfg["filename"],
        "octets": len(contenu),
        "hote": cfg["host"],
        "url_publique": cfg["url_publique"],
    }
