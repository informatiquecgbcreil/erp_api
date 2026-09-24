"""Construction Windows x64, sources et téléchargements vérifiés par empreinte.

python desktop/build.py --work C:/build/mcs --output C:/livraison --iscc C:/Inno/ISCC.exe
Utiliser Python 3.13 x64 et les exigences desktop/requirements-windows.lock.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[1]
DOWNLOADS = {
    "python-3.13.15-embed-amd64.zip": ("https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip", "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"),
    "postgresql-17.11-windows-x64.zip": ("https://sbp.enterprisedb.com/getfile.jsp?fileid=1260569", "b9424ee7bc60b52450ff910a3630225df32e633f3cb29c1d126d9299d59aea28"),
    "postgresql-18.6-windows-x64.zip": ("https://get.enterprisedb.com/postgresql/postgresql-18.6-4-windows-x64-binaries.zip", "1df55002afe95b945d934c078b13e82c1603fa546731e511d068aa983b4ead28"),
    "caddy_2.11.4_windows_amd64.zip": ("https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_windows_amd64.zip", "1708333f79e274c7697285afe6d592ab39314e0b131e9ec6bea08ad27df62ebf"),
    "vc_redist.x64.exe": ("https://aka.ms/vs/17/release/vc_redist.x64.exe", "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b"),
}


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(*args):
    subprocess.run([str(x) for x in args], check=True, cwd=REPO)


def icon(path):
    # Version ICO multirésolution du pictogramme vectoriel livré dans app/static.
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (1024, 1024))
    d = ImageDraw.Draw(im)
    def pts(values): return [(x*4,y*4) for x,y in values]
    teal, gold = "#135b63", "#f2b84b"
    d.rounded_rectangle((0,0,1023,1023),radius=224,fill=teal)
    d.polygon(pts([(40,121),(128,47),(216,121),(216,204),(40,204)]),fill="white")
    d.line(pts([(34,124),(128,44),(222,124)]),fill=gold,width=64,joint="curve")
    for x in (98,158):
        d.ellipse(((x-16)*4,111*4,(x+16)*4,143*4),fill=teal)
        d.rounded_rectangle(((x-28)*4,145*4,(x+28)*4,218*4),radius=112,fill=teal)
    d.rectangle((40*4,192*4,216*4,204*4),fill="white")
    d.ellipse((116*4,139*4,140*4,163*4),fill=gold)
    d.rounded_rectangle((109*4,161*4,147*4,211*4),radius=76,fill=gold)
    d.rectangle((108*4,191*4,149*4,211*4),fill="white")
    im.save(path.with_suffix(".png"))
    im.save(path, sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)], bitmap_format="bmp")


def build(args):
    work, output = args.work.resolve(), args.output.resolve()
    work.mkdir(parents=True, exist_ok=True); output.mkdir(parents=True, exist_ok=True)
    downloads = args.downloads.resolve() if args.downloads else work / "downloads"
    downloads.mkdir(exist_ok=True)
    for name, (url, digest) in DOWNLOADS.items():
        file = downloads / name
        if not file.exists():
            print("Téléchargement", name, flush=True); urllib.request.urlretrieve(url,file)
        if sha(file) != digest:
            raise RuntimeError("Empreinte incorrecte : " + name)
    payload = work / "payload"
    # Aucun effacement récursif implicite : choisir un dossier neuf pour reconstruire.
    if payload.exists():
        raise RuntimeError("Le dossier payload existe déjà. Choisir un autre --work.")
    payload.mkdir()
    for archive, target in [("python-3.13.15-embed-amd64.zip","python"), ("caddy_2.11.4_windows_amd64.zip","caddy")]:
        with zipfile.ZipFile(downloads / archive) as z: z.extractall(payload / target)
    # Deux moteurs séparés : 18 pour les nouvelles reprises, 17 pour les clusters existants.
    for archive, folder in [("postgresql-17.11-windows-x64.zip", "postgresql"), ("postgresql-18.6-windows-x64.zip", "postgresql18")]:
        with zipfile.ZipFile(downloads / archive) as z:
            for name in z.namelist():
                parts = Path(name).parts
                if len(parts) < 2 or parts[0] != "pgsql": continue
                if parts[1] not in {"bin","lib","share"} and "license" not in parts[1].lower(): continue
                target = payload / folder / Path(*parts[1:])
                if name.endswith("/"): target.mkdir(parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(name) as src, target.open("wb") as dest: shutil.copyfileobj(src,dest)
    python_dir = payload / "python"
    (python_dir / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\nimport site\n", encoding="ascii")
    lock = REPO / "desktop/requirements-windows.lock"
    if not lock.exists(): raise RuntimeError("Générer le verrou de dépendances avant la construction.")
    run(sys.executable, "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "--no-compile", "--target", python_dir / "Lib/site-packages", "-r", lock)
    # Liste Git explicite : jamais de copie des bases, uploads ou configurations locales.
    sources = subprocess.check_output(["git","ls-files","--cached"],cwd=REPO,text=True).splitlines()
    with zipfile.ZipFile(payload / "sources-Mon-Centre-Social.zip", "w", zipfile.ZIP_DEFLATED) as source_zip:
        for relative in sources:
            file = REPO / relative
            if not file.is_file(): continue
            source_zip.write(file,relative)
            if relative.startswith(("app/","migrations/","data/","static/")) or relative in {"config.py","wsgi.py","requirements.txt","LICENSE"}:
                dest = payload / "application" / relative
                dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(file,dest)
    (payload / "desktop").mkdir()
    shutil.copy2(REPO / "desktop/runtime.py", payload / "desktop/runtime.py")
    shutil.copy2(REPO / "desktop/migration.py", payload / "desktop/migration.py")
    shutil.copy2(REPO / "docs/GUIDE-WINDOWS.md", payload / "GUIDE-WINDOWS.md")
    shutil.copy2(downloads / "vc_redist.x64.exe",payload)
    icon(payload / "mon-centre-social.ico")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    run(compiler,"/nologo","/target:winexe","/platform:x64","/optimize+","/utf8output",
        "/out:" + str(payload / "MonCentreSocial.exe"), "/win32icon:" + str(payload / "mon-centre-social.ico"),
        "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.ServiceProcess.dll",
        "/r:System.Web.Extensions.dll", "/r:System.Security.dll", REPO / "desktop/MonCentreSocial.cs")
    (payload / "MonCentreSocial.exe.config").write_text('<?xml version="1.0"?><configuration><startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.7.2"/></startup></configuration>',encoding="utf-8")
    version = subprocess.check_output(["git","rev-parse","HEAD"],cwd=REPO,text=True).strip()
    base = subprocess.check_output(["git","merge-base","origin/main","HEAD"],cwd=REPO,text=True).strip()
    dirty = bool(subprocess.check_output(["git","status","--porcelain"],cwd=REPO,text=True).strip())
    manifest = {"version":"1.0.0-rc2", "source_commit":version, "base_commit":base, "uncommitted_changes":dirty,"platform":"Windows x64","components":DOWNLOADS,
                "files":{str(p.relative_to(payload)).replace("\\","/"):sha(p) for p in sorted(payload.rglob("*")) if p.is_file()}}
    (payload / "manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    run(args.iscc, "/DPayload=" + str(payload), "/DDeliverables=" + str(output), REPO / "desktop/installer.iss")
    installer = output / "Mon-Centre-Social-1.0.0-rc2-Setup-x64.exe"
    (output / (installer.name + ".sha256")).write_text(sha(installer) + "  " + installer.name + "\n",encoding="ascii")
    print("Livraison :", installer)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--work",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    p.add_argument("--iscc",type=Path,required=True); p.add_argument("--downloads",type=Path)
    build(p.parse_args())
