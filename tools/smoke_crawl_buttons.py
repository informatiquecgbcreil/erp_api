"""Audit non destructif des liens et boutons GET de l'application.

Usage:
  python tools/smoke_crawl_buttons.py
  python tools/smoke_crawl_buttons.py --start /qualite-donnees --max-pages 80

Le script ouvre les pages avec le client Flask authentifie, collecte les liens
et formulaires GET, puis signale les 404/500. Les formulaires POST sont
recenses mais jamais soumis.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app import create_app
from app.models import User


DEFAULT_STARTS = [
    "/dashboard",
    "/qualite-donnees",
    "/bilans-hub",
    "/bilans/qualite",
    "/documents",
    "/suivi-rappels",
    "/projets",
    "/partenaires/orientations",
    "/stats-impact/dashboard",
]

SKIP_PATH_PARTS = (
    "/logout",
    "/password-reset",
)

DESTRUCTIVE_WORDS = (
    "delete",
    "supprimer",
    "remove",
    "archive",
    "toggle",
    "reset",
    "reinitialiser",
    "action=delete",
    "action=cancel",
)


@dataclass
class Target:
    url: str
    source: str
    label: str
    kind: str


class LinkCollector(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[Target] = []
        self.get_forms: list[Target] = []
        self.post_forms: list[Target] = []
        self._current_link: dict | None = None
        self._current_form: dict | None = None

    def handle_starttag(self, tag: str, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = (attrs.get("href") or "").strip()
            if href:
                self._current_link = {"href": href, "text": ""}
        elif tag == "form":
            method = (attrs.get("method") or "get").strip().lower()
            action = (attrs.get("action") or self.base_url).strip()
            self._current_form = {
                "method": method,
                "action": action,
                "inputs": [],
                "text": "",
            }
        elif tag == "input" and self._current_form is not None:
            name = (attrs.get("name") or "").strip()
            value = attrs.get("value") or ""
            input_type = (attrs.get("type") or "text").strip().lower()
            if name and input_type not in {"submit", "button", "file", "password"}:
                self._current_form["inputs"].append((name, value))
        elif tag == "button" and self._current_form is not None:
            self._current_form["text"] += " " + (attrs.get("value") or "")

    def handle_data(self, data: str):
        if self._current_link is not None:
            self._current_link["text"] += data
        if self._current_form is not None:
            self._current_form["text"] += data

    def handle_endtag(self, tag: str):
        if tag == "a" and self._current_link is not None:
            href = self._current_link["href"]
            label = " ".join(self._current_link["text"].split()) or href
            self.links.append(Target(href, self.base_url, label, "link"))
            self._current_link = None
        elif tag == "form" and self._current_form is not None:
            method = self._current_form["method"]
            action = self._current_form["action"]
            label = " ".join(self._current_form["text"].split()) or action
            inputs = self._current_form["inputs"]
            url = action
            if method == "get" and inputs:
                sep = "&" if "?" in url else "?"
                url = url + sep + urlencode(inputs)
            target = Target(url, self.base_url, label, f"form:{method}")
            if method == "get":
                self.get_forms.append(target)
            else:
                self.post_forms.append(target)
            self._current_form = None


def _normalize_url(raw_url: str, source: str) -> str | None:
    if not raw_url or raw_url.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    absolute = urljoin("http://local.test" + source, raw_url)
    parsed = urlparse(absolute)
    if parsed.netloc and parsed.netloc != "local.test":
        return None
    path = parsed.path or "/"
    query = urlencode(parse_qsl(parsed.query, keep_blank_values=True), doseq=True)
    return urlunparse(("", "", path, "", query, ""))


def _should_skip(url: str, *, include_destructive: bool) -> bool:
    lowered = url.lower()
    if any(part in lowered for part in SKIP_PATH_PARTS):
        return True
    if not include_destructive and any(word in lowered for word in DESTRUCTIVE_WORDS):
        return True
    return False


def _is_html(resp) -> bool:
    content_type = (resp.headers.get("Content-Type") or "").lower()
    return "text/html" in content_type


def _status_level(status_code: int) -> str:
    if status_code >= 500:
        return "FAIL"
    if status_code == 404:
        return "FAIL"
    if status_code in {400, 401, 403, 405}:
        return "WARN"
    return "OK"


def run(args) -> int:
    app = create_app()
    failures = []
    warnings = []
    skipped_post: list[Target] = []
    checked: list[tuple[str, int, str, str, str]] = []

    with app.app_context():
        user_q = User.query
        user = user_q.filter_by(email=args.user).first() if args.user else None
        user = user or user_q.order_by(User.id.asc()).first()
        if not user:
            raise RuntimeError("Aucun utilisateur disponible pour l'audit.")

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

        starts = args.start or DEFAULT_STARTS
        queue = deque()
        for start in starts:
            normalized = _normalize_url(start, "/") or start
            queue.append(Target(normalized, "seed", "Point de depart", "seed"))

        seen: set[str] = set()
        while queue and len(seen) < args.max_pages:
            target = queue.popleft()
            normalized = _normalize_url(target.url, target.source) or target.url
            if normalized in seen or _should_skip(normalized, include_destructive=args.include_destructive):
                continue
            seen.add(normalized)

            try:
                resp = client.get(normalized, follow_redirects=False)
                status = int(resp.status_code)
                level = _status_level(status)
                checked.append((normalized, status, level, target.kind, target.source))
                if level == "FAIL":
                    failures.append((normalized, status, target.kind, target.label, target.source))
                    continue
                if level == "WARN":
                    warnings.append((normalized, status, target.kind, target.label, target.source))
                    continue

                if status in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("Location")
                    nxt = _normalize_url(location or "", normalized)
                    if nxt and nxt not in seen:
                        queue.append(Target(nxt, normalized, "Redirection", "redirect"))
                    continue

                if _is_html(resp):
                    html = resp.get_data(as_text=True)
                    collector = LinkCollector(normalized)
                    collector.feed(html)
                    skipped_post.extend(collector.post_forms)
                    for item in collector.links + collector.get_forms:
                        nxt = _normalize_url(item.url, normalized)
                        if not nxt or nxt in seen:
                            continue
                        if _should_skip(nxt, include_destructive=args.include_destructive):
                            continue
                        queue.append(Target(nxt, normalized, item.label, item.kind))
            except Exception as exc:
                failures.append((normalized, 0, target.kind, f"Exception: {exc}", target.source))

    print(f"Utilisateur: {getattr(user, 'email', '')}")
    print(f"Pages/liens GET testes: {len(checked)}")
    print(f"Erreurs bloquantes: {len(failures)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Formulaires POST recenses non soumis: {len(skipped_post)}")

    if failures:
        print("\n[FAIL]")
        for url, status, kind, label, source in failures[: args.limit]:
            print(f"- {status} {kind} {url} | {label} | depuis {source}")
    if warnings:
        print("\n[WARN]")
        for url, status, kind, label, source in warnings[: args.limit]:
            print(f"- {status} {kind} {url} | {label} | depuis {source}")
    if args.show_post and skipped_post:
        print("\n[POST non soumis]")
        for item in skipped_post[: args.limit]:
            print(f"- {item.url} | {item.label} | depuis {item.source}")

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="Email de l'utilisateur a injecter en session.")
    parser.add_argument("--start", action="append", help="URL de depart, repetable.")
    parser.add_argument("--max-pages", type=int, default=180)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--include-destructive", action="store_true")
    parser.add_argument("--show-post", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
