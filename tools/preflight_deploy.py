"""Préflight de déployabilité.

Objectif: fournir un diagnostic actionnable avant mise en prod sur une nouvelle structure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import DEFAULT_SECRET_KEY


def _mask_db_url(url: str) -> str:
    if not url:
        return "(sqlite fallback local)"
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(parsed.password, "***", 1)
            return parsed._replace(netloc=netloc).geturl()
        return url
    except Exception:
        return url


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    erp_env = (os.getenv("ERP_ENV", "development") or "development").strip().lower()
    secret_key = os.getenv("SECRET_KEY", "").strip()
    database_url = (os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL") or "").strip()
    public_base_url = os.getenv("ERP_PUBLIC_BASE_URL", "").strip()
    upload_dir = Path(os.getenv("APP_UPLOAD_DIR", Path("app/static/uploads").as_posix()))

    infos.append(f"ERP_ENV={erp_env}")
    infos.append(f"DATABASE_URL={_mask_db_url(database_url)}")
    infos.append(f"APP_UPLOAD_DIR={upload_dir}")

    if not secret_key:
        errors.append("SECRET_KEY non définie.")
    elif secret_key in {"change-me-in-production", DEFAULT_SECRET_KEY}:
        if erp_env == "production":
            errors.append("SECRET_KEY de démo détectée en production.")
        else:
            warnings.append("SECRET_KEY de démo détectée (acceptable en dev seulement).")

    if not database_url:
        warnings.append("Aucune DATABASE_URL: fallback SQLite local utilisé.")
    elif database_url.startswith("sqlite:///") and erp_env == "production":
        warnings.append("SQLite détecté en production (PostgreSQL recommandé).")

    if erp_env == "production" and not public_base_url:
        warnings.append("ERP_PUBLIC_BASE_URL vide (liens email/QR potentiellement incorrects).")

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        probe = upload_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover - dépend du FS
        errors.append(f"Dossier uploads non accessible en écriture: {exc}")

    print("=== Préflight déployabilité ===")
    for msg in infos:
        print(f"[INFO] {msg}")
    for msg in warnings:
        print(f"[WARN] {msg}")
    for msg in errors:
        print(f"[ERR]  {msg}")

    if errors:
        print("\nStatut: ECHEC (corriger les erreurs ci-dessus)")
        return 1

    print("\nStatut: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
