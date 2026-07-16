"""Intégration avec le « Portail des apprenants » (API REST externe).

- À l'inscription d'un stagiaire : crée son code apprenant sur le portail
  (idempotent) et le stocke sur sa fiche participant.
- Périodiquement (tâche planifiée) : récupère les tentatives (scores/durées)
  et les range dans nos stats (table portail_attempt), dédoublonnées par
  l'``id`` de la tentative et rapprochées du participant via l'externalId.

Le portail ne stocke aucune donnée personnelle : on ne lui transmet que notre
ID interne de stagiaire (externalId = Participant.id). Auth : en-tête
``x-api-token``. Aucune dépendance externe (urllib de la bibliothèque standard).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.utils.dates import utcnow


class PortailError(RuntimeError):
    """Erreur d'appel au portail (réseau, HTTP, réponse invalide)."""


def _config() -> tuple[str, str]:
    cfg = current_app.config
    base = (cfg.get("PORTAIL_BASE_URL") or "").strip().rstrip("/")
    token = (cfg.get("PORTAIL_TOKEN") or "").strip()
    return base, token


def portail_configure() -> bool:
    base, token = _config()
    return bool(base and token)


def _requete(methode: str, chemin: str, params: dict | None = None,
             body: dict | None = None, timeout: int = 15) -> dict:
    base, token = _config()
    if not (base and token):
        raise PortailError("Portail non configuré (PORTAIL_BASE_URL / PORTAIL_TOKEN).")

    url = base + chemin
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {"x-api-token": token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charge = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise PortailError(f"HTTP {exc.code} sur {chemin} : {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise PortailError(f"Erreur réseau vers le portail : {exc}") from exc

    if not charge:
        return {}
    try:
        return json.loads(charge)
    except ValueError as exc:
        raise PortailError(f"Réponse non-JSON du portail sur {chemin}.") from exc


# --------------------------------------------------------------------------
# Appels bas niveau
# --------------------------------------------------------------------------
def health() -> bool:
    """True si GET /api/health répond {"ok": true}."""
    return bool(_requete("GET", "/api/health").get("ok"))


def creer_apprenant(external_id) -> dict:
    """POST /api/integration/learners → { "code", "created" }. Idempotent."""
    return _requete("POST", "/api/integration/learners", body={"externalId": str(external_id)})


def recuperer_attempts(since: str | None) -> dict:
    """GET /api/integration/attempts?since=<ISO8601> → { generatedAt, attempts[] }."""
    params = {"since": since} if since else None
    return _requete("GET", "/api/integration/attempts", params=params)


# --------------------------------------------------------------------------
# Logique métier
# --------------------------------------------------------------------------
def assurer_code_portail(participant) -> bool:
    """Garantit que le participant possède un code apprenant (à l'inscription).

    Idempotent et **non bloquant** : si le portail n'est pas configuré ou si
    l'appel échoue, on journalise et on renvoie False (un nouvel essai aura
    lieu plus tard) sans jamais faire échouer l'inscription.
    """
    if getattr(participant, "portail_code", None):
        return True
    if not portail_configure():
        return False
    try:
        reponse = creer_apprenant(participant.id)
        code = (reponse or {}).get("code")
        if not code:
            return False
        participant.portail_code = code
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.warning(
            "Code portail non créé pour le participant %s (réessai ultérieur).",
            getattr(participant, "id", "?"),
            exc_info=True,
        )
        return False


def _parse_dt(valeur):
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def synchroniser_attempts() -> dict:
    """Récupère les tentatives depuis le portail et les range (upsert dédupliqué).

    - Ignore les lignes dont externalId est null.
    - Dédoublonne par l'``id`` de la tentative (clé unique) : la borne ``since``
      étant inclusive, une même tentative peut revenir → on met à jour au lieu
      de dupliquer.
    - Mémorise ``generatedAt`` comme nouveau ``since`` pour le prochain passage,
      **uniquement** après une récupération réussie (sinon réessai au prochain
      passage, sans perte).

    Lève ``PortailError`` en cas d'échec réseau (le curseur n'est pas avancé).
    """
    from app.models import Participant, PortailAttempt, PortailSyncState

    if not portail_configure():
        raise PortailError("Portail non configuré (PORTAIL_BASE_URL / PORTAIL_TOKEN).")

    etat = db.session.get(PortailSyncState, 1)
    if etat is None:
        etat = PortailSyncState(id=1)
        db.session.add(etat)
        db.session.commit()

    data = recuperer_attempts(etat.last_since)  # PortailError -> réessai au prochain passage
    attempts = data.get("attempts") or []
    generated_at = data.get("generatedAt")

    nb_nouvelles = 0
    nb_ignorees = 0
    for a in attempts:
        external_id = a.get("externalId")
        attempt_id = a.get("id")
        if external_id is None or attempt_id in (None, ""):
            nb_ignorees += 1
            continue
        attempt_id = str(attempt_id)

        participant_id = None
        try:
            participant_id = int(external_id)
        except (TypeError, ValueError):
            participant_id = None
        if participant_id is not None and db.session.get(Participant, participant_id) is None:
            participant_id = None  # externalId inconnu chez nous : on garde la trace sans rattacher

        champs = dict(
            external_id=str(external_id),
            participant_id=participant_id,
            activity=a.get("activity"),
            activity_type=a.get("activityType"),
            theme=a.get("theme"),
            score=a.get("score"),
            max_score=a.get("maxScore"),
            pct=a.get("pct"),
            duration_ms=a.get("durationMs"),
            finished_at=_parse_dt(a.get("finishedAt")),
        )

        existant = PortailAttempt.query.filter_by(attempt_id=attempt_id).first()
        if existant:
            for cle, val in champs.items():
                setattr(existant, cle, val)
        else:
            db.session.add(PortailAttempt(attempt_id=attempt_id, **champs))
            nb_nouvelles += 1

    if generated_at:
        etat.last_since = str(generated_at)
    etat.last_run_at = utcnow()
    etat.last_status = f"{nb_nouvelles} nouvelle(s), {len(attempts)} reçue(s), {nb_ignorees} ignorée(s)"
    db.session.commit()

    return {
        "recues": len(attempts),
        "nouvelles": nb_nouvelles,
        "ignorees": nb_ignorees,
        "since": etat.last_since,
    }
