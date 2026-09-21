"""Recette du payload Windows sur une base NEUVE, sans installer de service.

python desktop/smoke.py --payload C:/build/mcs/payload --data-root "C:/recette/Centre Équipe"
Refuse tout dossier de données préexistant. N'envoie aucun e-mail.
"""
import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def smoke(payload, root):
    payload, root = payload.resolve(), root.resolve()
    if root.exists():
        raise RuntimeError("La recette exige un dossier neuf ; aucune base existante n'est utilisée.")
    root.mkdir(parents=True)
    cfg = dict(data_root=str(root), organization="Centre de recette Équipe", admin_name="Direction Équipe",
               admin_email="recette@example.test", admin_password=" " + secrets.token_urlsafe(25) + " ",
               db_password=secrets.token_urlsafe(32), db_admin_password=secrets.token_urlsafe(32),
               secret_key=secrets.token_urlsafe(40), db_port=free_port(), web_port=free_port(),
               https_port=free_port(), kiosk_http_port=free_port(), lan_ip="127.0.0.1",
               network=True, hostname="localhost", modules=["presences", "statistiques"],
               smtp_host="", smtp_port=587, smtp_user="", smtp_password="", smtp_sender="")
    cfg["url"] = f"https://localhost:{cfg['https_port']}"
    cfg["kiosk_url"] = f"http://127.0.0.1:{cfg['kiosk_http_port']}"
    results = {}
    process = None
    log = (root / "smoke-supervisor.log").open("wb")

    def stop(p):
        (root / "runtime/stop").write_text("1")
        if p.wait(timeout=100) != 0:
            raise RuntimeError("Le runtime s'est terminé en erreur.")

    def start():
        nonlocal process
        process = subprocess.Popen([str(payload / "python/python.exe"), "-B", str(payload / "desktop/runtime.py"), "--supervise"],
                                   stdin=subprocess.PIPE, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        process.stdin.write(json.dumps(cfg, ensure_ascii=False).encode("utf-8")); process.stdin.close()
        for _ in range(480):
            if process.poll() is not None:
                raise RuntimeError("Échec du démarrage : consulter smoke-supervisor.log.")
            if (root / "runtime/ready").exists():
                return process
            time.sleep(0.5)
        raise RuntimeError("Délai de démarrage dépassé.")

    try:
        start(); results["base_vierge_et_chemin_accentue"] = True
        context = ssl.create_default_context(cafile=str(root / "runtime/tls/pki/authorities/local/root.crt"))
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=context))
        def get(path):
            try:
                with opener.open(cfg["url"] + path, timeout=30) as r:
                    return r.status, r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8")
        def get_mobile(path):
            try:
                with urllib.request.urlopen(cfg["kiosk_url"] + path, timeout=30) as r:
                    return r.status, r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8")
        code, body = get("/"); assert code == 200
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', body).group(1)
        data = urllib.parse.urlencode(dict(email=cfg["admin_email"], password=cfg["admin_password"], csrf_token=csrf)).encode()
        request = urllib.request.Request(cfg["url"] + "/", data=data, headers={"Referer": cfg["url"] + "/"})
        with opener.open(request, timeout=30) as r:
            assert r.status == 200 and "/dashboard" in r.url
        results["https_certificat_verifie_connexion_csrf"] = True
        for path in ("/admin/modules", "/activite/", "/participants/", "/dashboard"):
            assert get(path)[0] == 200, path
        for path in ("/rh", "/caisse", "/salles/", "/setup/", "/media/justifs/secret.pdf"):
            assert get(path)[0] == 404, path
        results["modules_et_assistant_web_proteges"] = True
        assert get_mobile("/kiosk/")[0] == 200
        assert get_mobile("/dashboard")[0] == 403
        results["kiosque_mobile_lan_sans_certificat"] = True
        for _ in range(80):
            if list((root / "backups").glob("*.sha256")): break
            time.sleep(0.5)
        assert list((root / "backups").glob("*.sql"))
        results["sauvegarde_postgresql_automatique"] = True
        stop(process); process = None
        assert not (root / "postgresql/postmaster.pid").exists()
        results["arret_propre"] = True
        start(); assert get("/dashboard")[0] == 200
        results["redemarrage_compte_et_session_conserves"] = True
        stop(process); process = None
        logtext = (root / "logs/runtime.log").read_text(encoding="utf-8", errors="replace")
        assert all(cfg[k] not in logtext for k in ("admin_password", "db_password", "db_admin_password", "secret_key"))
        results["journaux_sans_secrets"] = True
    finally:
        if process is not None and process.poll() is None:
            try: stop(process)
            except Exception:
                process.terminate(); process.wait(timeout=10)
                subprocess.run([str(payload / "postgresql/bin/pg_ctl.exe"), "-D", str(root / "postgresql"), "-w", "-m", "fast", "stop"],
                               stdout=log, stderr=log, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        log.close()
        (root / "resultats.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--payload", type=Path, required=True)
    p.add_argument("--data-root", type=Path, required=True)
    args = p.parse_args()
    smoke(args.payload, args.data_root)
