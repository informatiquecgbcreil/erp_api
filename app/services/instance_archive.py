"""Archives portables des fichiers métier, indépendantes du dossier du code.

Le format 2 conserve uploads ET instance. Les anciennes archives d'uploads
restent lisibles. Les liens, chemins Windows ambigus et archives démesurées
sont refusés avant d'écrire quoi que ce soit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

MANIFEST = "mcs-backup.json"
MAX_FILES = 200_000
MAX_BYTES = 100 * 1024**3
PATH_COLUMNS = {"signature_path", "file_path", "docx_path", "pdf_path",
                "corrected_docx_path", "corrected_pdf_path"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files_under(root, excluded=()):
    root = Path(root).absolute()
    if root.is_symlink():
        raise RuntimeError("Un dossier métier est un lien : reprise manuelle nécessaire.")
    excluded = [Path(p).resolve() for p in excluded]
    if not root.exists():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs) + files:
            path = Path(directory) / name
            if any(path.resolve().is_relative_to(p) for p in excluded):
                if name in dirs:
                    dirs.remove(name)
                continue
            if path.is_symlink() or (os.name == "nt" and path.lstat().st_file_attributes & 0x400):
                raise RuntimeError("Un lien de fichier ou de dossier empêche la copie complète.")
            if name in files:
                yield path, path.relative_to(root).as_posix()


def create_archive(destination, roots, excluded=()):
    roots = {k: Path(v).absolute() for k, v in roots.items() if v}
    manifest = {"format": 2, "roots": {k: str(v) for k, v in roots.items()}, "files": {}}
    temporary = Path(str(destination) + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for label, root in roots.items():
                if label not in {"instance", "uploads"}:
                    raise ValueError("Racine d'archive inconnue.")
                for path, relative in files_under(root, (*excluded, temporary, destination)):
                    name = label + "/" + relative
                    before = digest(path)
                    archive.write(path, name)
                    if digest(path) != before:
                        raise RuntimeError("Un fichier a changé pendant la sauvegarde. Recommencez hors saisies.")
                    manifest["files"][name] = before
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def validate_members(archive):
    names, size = set(), 0
    for member in archive.infolist():
        name = member.filename
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith("/") or "\\" in name or ":" in name
                or any(p in {".", ".."} or p.endswith((".", " ")) for p in parts)
                or any(ord(c) < 32 for c in name)
                or stat.S_ISLNK(member.external_attr >> 16)):
            raise RuntimeError("Archive refusée : chemin de fichier dangereux.")
        canonical = name.rstrip("/").casefold()
        if canonical in names:
            raise RuntimeError("Archive refusée : noms de fichiers en double.")
        names.add(canonical)
        size += member.file_size
        if len(names) > MAX_FILES or size > MAX_BYTES:
            raise RuntimeError("Archive trop volumineuse pour une restauration automatique.")


def stage_archive(source, staging):
    """Vérifie et décompresse avant toute restauration de la base."""
    staging = Path(staging)
    with zipfile.ZipFile(source) as archive:
        validate_members(archive)
        manifest = None
        if MANIFEST in archive.namelist():
            if archive.getinfo(MANIFEST).file_size > 32 * 1024**2:
                raise RuntimeError("Manifeste de sauvegarde trop volumineux.")
            manifest = json.loads(archive.read(MANIFEST))
            if manifest.get("format") != 2 or set(manifest.get("roots", {})) - {"instance", "uploads"}:
                raise RuntimeError("Format de sauvegarde inconnu.")
            expected = set(manifest.get("files", {}))
            actual = {x.filename for x in archive.infolist() if not x.is_dir() and x.filename != MANIFEST}
            if expected != actual or any(n.split("/", 1)[0] not in {"uploads", "instance"} for n in actual):
                raise RuntimeError("Le manifeste ne correspond pas au contenu de l'archive.")
        for member in archive.infolist():
            if member.filename == MANIFEST or member.is_dir():
                continue
            target = staging / (member.filename if manifest else "uploads/" + member.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            if manifest and digest(target) != manifest["files"][member.filename]:
                raise RuntimeError("Un fichier de l'archive est corrompu.")
    return manifest


def install_staged(staging, roots):
    """Copie contrôlée, en conservant les fichiers absents des vieux lots."""
    for label, root in roots.items():
        if not root:
            continue
        root = Path(root).resolve()
        for source, relative in files_under(Path(staging) / label):
            target = root / relative
            if not target.resolve().is_relative_to(root):
                raise RuntimeError("Un lien sort du dossier de restauration.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def remap_paths(connection, old_roots, new_roots):
    """Réécrit uniquement les colonnes de chemins métier, jamais les notes."""
    from sqlalchemy import MetaData, Table, inspect, select, update
    inspector = inspect(connection)
    for table_name in inspector.get_table_names():
        columns = PATH_COLUMNS & {c["name"] for c in inspector.get_columns(table_name)}
        if not columns:
            continue
        table = Table(table_name, MetaData(), autoload_with=connection)
        for column in columns:
            for (old,) in connection.execute(select(table.c[column]).distinct()):
                if not old:
                    continue
                normalized = old.replace("\\", "/")
                for label, prefix in old_roots.items():
                    if label not in new_roots or not new_roots[label]:
                        continue
                    prefix = prefix.replace("\\", "/").rstrip("/") + "/"
                    compare = str.casefold if ":" in prefix else lambda s: s
                    if compare(normalized).startswith(compare(prefix)):
                        relative = normalized[len(prefix):]
                        if ".." in PurePosixPath(relative).parts:
                            raise RuntimeError("Chemin métier non portable dans la base.")
                        target = str(Path(new_roots[label]) / relative)
                        connection.execute(update(table).where(table.c[column] == old).values({column: target}))
                        break
