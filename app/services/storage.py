import os
from flask import current_app, send_from_directory, abort, url_for


def authorize_public_media(relpath):
    """Les justificatifs passent exclusivement par leurs routes métier cloisonnées."""
    from flask_login import current_user
    from werkzeug.utils import secure_filename
    from app.models import InstanceSettings
    from app.rbac import can
    path = _normalize_relpath(relpath)
    parts = path.split("/")
    if ".." in parts or ":" in path:
        abort(404)
    settings = InstanceSettings.query.first()
    if parts[0] == "branding" and settings and path in {
        settings.app_logo_path, settings.organization_logo_path
    }:
        return
    if not current_user.is_authenticated or not current_user.is_active:
        abort(401)
    if parts[0] == "bilans_lourds" and len(parts) == 4 and can("bilans:view"):
        scope = secure_filename(current_user.secteur_assigne or "")
        if can("scope:all_secteurs") or (scope and parts[2] == scope):
            return
    # justifs, factures et projets : aucun contournement des droits par /media.
    abort(404)


def get_upload_root() -> str:
    root = current_app.config.get("APP_UPLOAD_DIR")
    if not root:
        root = os.path.join(current_app.root_path, "..", "static", "uploads")
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    return root


def _safe_abs_under_root(*parts: str) -> str:
    root = get_upload_root()
    root = os.path.realpath(root)
    abs_path = os.path.realpath(os.path.join(root, *parts))
    try:
        allowed = os.path.commonpath([root, abs_path]) == root
    except ValueError:
        allowed = False
    if not allowed:
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
    resolved = _safe_abs_under_root(relpath)
    directory = os.path.dirname(resolved)
    filename = os.path.basename(relpath)
    return send_from_directory(directory, filename, as_attachment=as_attachment, download_name=download_name)


def media_url(relpath: str) -> str:
    relpath = _normalize_relpath(relpath)
    return url_for("media_file", filename=relpath)
