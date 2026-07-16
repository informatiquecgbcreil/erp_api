import os
from flask import current_app, send_from_directory, abort, url_for


def get_upload_root() -> str:
    root = current_app.config.get("APP_UPLOAD_DIR")
    if not root:
        root = os.path.join(current_app.root_path, "..", "static", "uploads")
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    return root


def _safe_abs_under_root(*parts: str) -> str:
    root = get_upload_root()
    abs_path = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([root, abs_path]) != root:
        abort(400)
    return abs_path


def ensure_upload_subdir(*parts: str) -> str:
    folder = _safe_abs_under_root(*parts)
    os.makedirs(folder, exist_ok=True)
    return folder


def media_relpath(*parts: str) -> str:
    cleaned = []
    for part in parts:
        if not part:
            continue
        cleaned.append(str(part).strip("/\\"))
    return "/".join(cleaned)


def _normalize_relpath(relpath: str) -> str:
    relpath = (relpath or "").strip().replace("\\", "/").strip("/")
    if relpath.startswith("uploads/"):
        relpath = relpath[len("uploads/"):]
    return relpath


def send_media_file(relpath: str, *, as_attachment: bool = False, download_name: str | None = None):
    relpath = _normalize_relpath(relpath)
    if not relpath:
        abort(404)
    root = get_upload_root()
    directory = os.path.abspath(os.path.join(root, os.path.dirname(relpath)))
    if os.path.commonpath([root, directory]) != root:
        abort(400)
    filename = os.path.basename(relpath)
    return send_from_directory(directory, filename, as_attachment=as_attachment, download_name=download_name)


def media_url(relpath: str) -> str:
    relpath = _normalize_relpath(relpath)
    return url_for("media_file", filename=relpath)
