"""Audit des formulaires POST sur une copie temporaire de la base.

Usage:
  python tools/smoke_post_buttons_copy.py
  python tools/smoke_post_buttons_copy.py --start /qualite-donnees --max-pages 120

Le script copie instance/database.db, force l'application a utiliser cette copie,
parcourt les pages GET, puis soumet uniquement les formulaires POST "action"
qui ne contiennent pas de champ libre. Les formulaires de creation/saisie sont
recenses mais ignores pour eviter les faux positifs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


DEFAULT_STARTS = [
    "/dashboard",
    "/qualite-donnees",
    "/bilans-hub",
    "/documents",
    "/suivi-rappels",
    "/projets",
    "/partenaires/orientations",
    "/stats-impact/dashboard",
]

SKIP_GET_PARTS = (
    "/logout",
    "/password-reset",
)

SKIP_POST_PARTS = (
    "/logout",
    "/ui-mode",
    "/password-reset",
)

FREE_INPUT_TYPES = {
    "",
    "text",
    "email",
    "password",
    "number",
    "date",
    "datetime-local",
    "time",
    "search",
    "tel",
    "url",
    "file",
}

CSRF_FIELD_NAMES = {"csrf", "csrf_token"}


@dataclass
class GetTarget:
    url: str
    source: str
    label: str


@dataclass
class PostForm:
    action: str
    source: str
    label: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    free_controls: list[str] = field(default_factory=list)


class PostAwareCollector(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[GetTarget] = []
        self.post_forms: list[PostForm] = []
        self._link: dict | None = None
        self._form: dict | None = None
        self._textarea_name: str | None = None

    def handle_starttag(self, tag: str, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = (attrs.get("href") or "").strip()
            if href:
                self._link = {"href": href, "text": ""}
            return

        if tag == "form":
            method = (attrs.get("method") or "get").strip().lower()
            action = (attrs.get("action") or self.base_url).strip()
            self._form = {
                "method": method,
                "action": action,
                "text": "",
                "fields": [],
                "free": [],
            }
            return

        if self._form is None:
            return

        if tag == "input":
            name = (attrs.get("name") or "").strip()
            input_type = (attrs.get("type") or "text").strip().lower()
            value = attrs.get("value") or ""
            if input_type in {"submit", "button", "image"}:
                if name:
                    self._form["fields"].append((name, value))
                self._form["text"] += " " + (attrs.get("value") or "")
                return
            if not name:
                return
            if input_type in FREE_INPUT_TYPES:
                self._form["free"].append(name)
            else:
                self._form["fields"].append((name, value))
        elif tag == "textarea":
            name = (attrs.get("name") or "").strip()
            if name:
                self._form["free"].append(name)
                self._textarea_name = name
        elif tag == "select":
            name = (attrs.get("name") or "").strip()
            if name:
                self._form["free"].append(name)
        elif tag == "button":
            name = (attrs.get("name") or "").strip()
            value = attrs.get("value") or ""
            if name:
                self._form["fields"].append((name, value))
            self._form["text"] += " " + value

    def handle_data(self, data: str):
        if self._link is not None:
            self._link["text"] += data
        if self._form is not None:
            self._form["text"] += data

    def handle_endtag(self, tag: str):
        if tag == "a" and self._link is not None:
            href = self._link["href"]
            label = " ".join(self._link["text"].split()) or href
            self.links.append(GetTarget(href, self.base_url, label))
            self._link = None
        elif tag == "textarea":
            self._textarea_name = None
        elif tag == "form" and self._form is not None:
            if self._form["method"] == "post":
                label = " ".join(self._form["text"].split()) or self._form["action"]
                self.post_forms.append(PostForm(
                    action=self._form["action"],
                    source=self.base_url,
                    label=label,
                    fields=list(self._form["fields"]),
                    free_controls=list(self._form["free"]),
                ))
            self._form = None


def normalize_url(raw_url: str, source: str) -> str | None:
    if not raw_url or raw_url.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    absolute = urljoin("http://local.test" + source, raw_url)
    parsed = urlparse(absolute)
    if parsed.netloc and parsed.netloc != "local.test":
        return None
    return urlunparse(("", "", parsed.path or "/", "", parsed.query, ""))


def should_skip(url: str, parts: tuple[str, ...]) -> bool:
    lowered = (url or "").lower()
    return any(part in lowered for part in parts)


def prepare_database_copy(keep_copy: bool) -> Path:
    source = ROOT_DIR / "instance" / "database.db"
    if not source.exists():
        raise RuntimeError(f"Base source introuvable: {source}")
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    if keep_copy:
        target = ROOT_DIR / "instance" / f"smoke_post_{suffix}.db"
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="appgestion_smoke_"))
        target = tmp_dir / "database.db"
    shutil.copy2(source, target)
    return target


def configure_database(db_path: Path) -> None:
    uri = "sqlite:///" + str(db_path).replace("\\", "/")
    os.environ["SQLALCHEMY_DATABASE_URI"] = uri
    os.environ["DB_AUTO_UPGRADE_ON_START"] = "0"
    os.environ.setdefault("SECRET_KEY", "smoke-test-secret")

    # Config lit l'environnement a l'import ; on met aussi la classe a jour
    # pour les creations d'app successives sur des copies differentes.
    from config import Config

    Config.SQLALCHEMY_DATABASE_URI = uri
    Config.DB_AUTO_UPGRADE_ON_START = False
    Config.SECRET_KEY = os.environ["SECRET_KEY"]


def cleanup_database_copy(db_path: Path, keep_copy: bool) -> None:
    if keep_copy:
        return
    try:
        shutil.rmtree(db_path.parent)
    except Exception:
        pass


def dispose_app(app) -> None:
    from app.extensions import db

    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def as_post_data(fields: list[tuple[str, str]]) -> dict[str, str]:
    data: dict[str, str] = {}
    for name, value in fields:
        data[name] = value
    return data


def stable_fields(fields: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (name, value)
        for name, value in fields
        if name.strip().lower() not in CSRF_FIELD_NAMES
    ))


def make_authenticated_client(create_app, User, user_email: str | None):
    with contextlib.redirect_stdout(io.StringIO()):
        app = create_app()
    with app.app_context():
        user_q = User.query
        user = user_q.filter_by(email=user_email).first() if user_email else None
        user = user or user_q.order_by(User.id.asc()).first()
        if not user:
            raise RuntimeError("Aucun utilisateur disponible pour l'audit.")

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    return app, client


def find_fresh_form(client, original: PostForm, action: str) -> PostForm | None:
    source = normalize_url(original.source, "/") or original.source
    resp = client.get(source, follow_redirects=False)
    if resp.status_code >= 400 or "text/html" not in (resp.headers.get("Content-Type") or "").lower():
        return None

    collector = PostAwareCollector(source)
    collector.feed(resp.get_data(as_text=True))

    expected_fields = stable_fields(original.fields)
    action_matches = []
    for candidate in collector.post_forms:
        candidate_action = normalize_url(candidate.action, candidate.source)
        if candidate_action != action:
            continue
        action_matches.append(candidate)
        if stable_fields(candidate.fields) == expected_fields:
            return candidate

    original_label = " ".join(original.label.split())
    for candidate in action_matches:
        if " ".join(candidate.label.split()) == original_label:
            return candidate

    return action_matches[0] if len(action_matches) == 1 else None


def run(args) -> int:
    db_copy = prepare_database_copy(args.keep_copy)
    configure_database(db_copy)

    from app import create_app
    from app.models import User

    app, client = make_authenticated_client(create_app, User, args.user)
    checked_get = []
    post_forms: list[PostForm] = []
    skipped_free: list[PostForm] = []
    post_failures = []
    post_warnings = []

    try:
        queue = deque(GetTarget(start, "seed", "Point de depart") for start in (args.start or DEFAULT_STARTS))
        seen_get: set[str] = set()

        while queue and len(seen_get) < args.max_pages:
            target = queue.popleft()
            url = normalize_url(target.url, target.source) or target.url
            if url in seen_get or should_skip(url, SKIP_GET_PARTS):
                continue
            seen_get.add(url)
            resp = client.get(url, follow_redirects=False)
            checked_get.append((url, resp.status_code))
            if resp.status_code >= 400:
                continue
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                continue

            collector = PostAwareCollector(url)
            collector.feed(resp.get_data(as_text=True))
            post_forms.extend(collector.post_forms)
            for link in collector.links:
                nxt = normalize_url(link.url, url)
                if nxt and nxt not in seen_get and not should_skip(nxt, SKIP_GET_PARTS):
                    queue.append(GetTarget(nxt, url, link.label))
    finally:
        dispose_app(app)
        cleanup_database_copy(db_copy, args.keep_copy)

    seen_posts: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    submitted = 0
    for form in post_forms:
        action = normalize_url(form.action, form.source)
        if not action or should_skip(action, SKIP_POST_PARTS):
            continue
        if form.free_controls and not args.include_free_forms:
            skipped_free.append(form)
            continue
        signature = (action, stable_fields(form.fields))
        if signature in seen_posts:
            continue
        seen_posts.add(signature)

        post_copy = prepare_database_copy(False)
        submit_app = None
        try:
            configure_database(post_copy)
            submit_app, submit_client = make_authenticated_client(create_app, User, args.user)
            fresh_form = find_fresh_form(submit_client, form, action)
            if fresh_form is None:
                post_warnings.append((action, 0, "Formulaire frais introuvable: " + form.label, form.source))
                continue
            fresh_action = normalize_url(fresh_form.action, fresh_form.source) or action
            submitted += 1
            resp = submit_client.post(fresh_action, data=as_post_data(fresh_form.fields), follow_redirects=False)
            if resp.status_code >= 500 or resp.status_code == 404:
                post_failures.append((action, resp.status_code, form.label, form.source))
            elif resp.status_code in {400, 401, 403, 405}:
                post_warnings.append((action, resp.status_code, form.label, form.source))
        except Exception as exc:
            post_failures.append((action, 0, f"Exception: {exc}", form.source))
        finally:
            if submit_app is not None:
                try:
                    dispose_app(submit_app)
                except Exception:
                    pass
            try:
                cleanup_database_copy(post_copy, False)
            except Exception:
                pass

    print(f"Copie DB: {db_copy}")
    print(f"Pages GET ouvertes pour collecter les POST: {len(checked_get)}")
    print(f"Formulaires POST trouves: {len(post_forms)}")
    print(f"POST soumis sur copie: {submitted}")
    print(f"POST ignores car champs libres: {len(skipped_free)}")
    print(f"Erreurs POST bloquantes: {len(post_failures)}")
    print(f"Warnings POST: {len(post_warnings)}")

    if post_failures:
        print("\n[FAIL POST]")
        for url, status, label, source in post_failures[: args.limit]:
            print(f"- {status} {url} | {label} | depuis {source}")
    if post_warnings:
        print("\n[WARN POST]")
        for url, status, label, source in post_warnings[: args.limit]:
            print(f"- {status} {url} | {label} | depuis {source}")
    if args.show_skipped and skipped_free:
        print("\n[POST ignores - champs libres]")
        for form in skipped_free[: args.limit]:
            action = normalize_url(form.action, form.source) or form.action
            print(f"- {action} | {form.label} | champs: {', '.join(form.free_controls[:8])} | depuis {form.source}")

    return 1 if post_failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="Email de l'utilisateur a injecter en session.")
    parser.add_argument("--start", action="append", help="URL de depart, repetable.")
    parser.add_argument("--max-pages", type=int, default=220)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--include-free-forms", action="store_true")
    parser.add_argument("--show-skipped", action="store_true")
    parser.add_argument("--keep-copy", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
