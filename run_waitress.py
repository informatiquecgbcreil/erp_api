import os
from waitress import serve
from wsgi import app

def _safe_print(msg: str) -> None:
    """
    Evite que Windows Services / NSSM crashe sur l'encodage (cp1252).
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        # Fallback brutal : on enlève les caractères non encodables
        print(msg.encode("ascii", "ignore").decode("ascii"))

if __name__ == "__main__":
    host = os.environ.get("ERP_HOST", "127.0.0.1")
    port = int(os.environ.get("ERP_PORT", "8000"))
    threads = int(os.environ.get("ERP_THREADS", "12"))

    _safe_print("Starting ERP (PostgreSQL/SQLite compatible) ...")
    _safe_print(f"Host={host}  Port={port}  Threads={threads}")
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        # Masquer le mot de passe à l'affichage
        safe = db_url
        if "://" in safe and "@" in safe:
            # masque tout ce qui est entre :// et @ en gardant user
            try:
                scheme, rest = safe.split("://", 1)
                creds, tail = rest.split("@", 1)
                if ":" in creds:
                    user = creds.split(":", 1)[0]
                    creds = f"{user}:***"
                safe = f"{scheme}://{creds}@{tail}"
            except Exception:
                pass
        _safe_print(f"DATABASE_URL={safe}")

    # Derrière nginx (ou un autre proxy local) : ERP_TRUSTED_PROXY=127.0.0.1
    # fait lire à waitress l'adresse réelle transmise par X-Forwarded-For
    # (mineur de l'audit : sans lui, tous les appareils partageaient les
    # compteurs anti-abus du kiosque). Sans réglage : comportement inchangé.
    proxy = os.environ.get("ERP_TRUSTED_PROXY", "").strip()
    options = {}
    if proxy:
        options = {
            "trusted_proxy": proxy,
            "trusted_proxy_count": int(os.environ.get("ERP_TRUSTED_PROXY_COUNT", "1")),
            "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
            "clear_untrusted_proxy_headers": True,
        }
        _safe_print(f"Proxy de confiance : {proxy}")
    serve(app, host=host, port=port, threads=threads, **options)
