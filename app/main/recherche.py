from time import perf_counter


from flask import (
    render_template, request, url_for, current_app, jsonify
)
import unicodedata
from flask_login import login_required, current_user
from app.rbac import can

from app.extensions import db
from app.models import (
    Subvention,
    Projet,
    AtelierActivite,
    SessionActivite,
    PresenceActivite,
    Participant,
    Partenaire,
    Quartier,
)


from app.main.common import bp


SEARCH_TYPE_PRIORITY = {
    "Participant": 0,
    "Quartier": 1,
    "Projet": 2,
    "Subvention": 3,
    "Atelier": 4,
    "Partenaire": 5,
}

SEARCH_TYPE_ALIASES = {
    "participant": "Participant",
    "participants": "Participant",
    "usager": "Participant",
    "usagers": "Participant",
    "quartier": "Quartier",
    "quartiers": "Quartier",
    "zone": "Quartier",
    "zones": "Quartier",
    "projet": "Projet",
    "projets": "Projet",
    "subvention": "Subvention",
    "subventions": "Subvention",
    "financement": "Subvention",
    "financements": "Subvention",
    "atelier": "Atelier",
    "ateliers": "Atelier",
    "activité": "Atelier",
    "activite": "Atelier",
    "activités": "Atelier",
    "activites": "Atelier",
    "partenaire": "Partenaire",
    "partenaires": "Partenaire",
}


def _normalize_search_text(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return "".join(ch for ch in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(ch))


def _parse_search_query(term: str) -> dict:
    raw_parts = [tok.strip() for tok in (term or "").split() if tok.strip()]
    parsed = {"type_filter": None, "secteur_filter": None, "quartier_filter": None, "tokens": []}
    for part in raw_parts:
        low = part.lower()
        if low.startswith("type:"):
            parsed["type_filter"] = (part.split(":", 1)[1] or "").strip().lower() or None
            continue
        if low.startswith("secteur:"):
            parsed["secteur_filter"] = (part.split(":", 1)[1] or "").strip() or None
            continue
        if low.startswith("quartier:"):
            parsed["quartier_filter"] = (part.split(":", 1)[1] or "").strip() or None
            continue
        parsed["tokens"].append(part)
    return parsed


def _run_global_search(term: str, *, panel_limit: int = 20, page_limit: int = 100):
    start_ts = perf_counter()
    term = (term or "").strip()
    if len(term) < 2:
        return {"results": [], "query": term, "facets": {"types": {}, "secteurs": {}}, "debug": {"duration_ms": 0}}

    parsed = _parse_search_query(term)
    sql_tokens = [tok.strip().lower() for tok in parsed["tokens"] if tok.strip()]
    tokens = [_normalize_search_text(tok) for tok in sql_tokens if _normalize_search_text(tok)]
    if not tokens or not sql_tokens:
        return {"results": [], "query": term, "facets": {"types": {}, "secteurs": {}}, "debug": {"duration_ms": 0}}

    type_filter = parsed["type_filter"]
    secteur_filter = (parsed["secteur_filter"] or "").strip()
    quartier_filter = _normalize_search_text(parsed["quartier_filter"] or "")

    has_global_scope = can("scope:all_secteurs")
    user_secteur = (getattr(current_user, "secteur_assigne", "") or "").strip()
    if secteur_filter and (not has_global_scope) and secteur_filter.lower() != user_secteur.lower():
        return {"results": [], "query": term, "facets": {"types": {}, "secteurs": {}}, "debug": {"duration_ms": 0}}

    active_secteur_filter = secteur_filter or (None if has_global_scope else user_secteur)
    wanted_type = SEARCH_TYPE_ALIASES.get(type_filter or "")
    dialect_name = ((db.session.bind.dialect.name if db.session.bind else "") or "").lower()
    results: list[dict] = []
    candidate_limit = max(panel_limit, min(max(page_limit, 20), 120))
    fetch_limit = max(candidate_limit * 3, 24)

    def _like(column, pattern: str):
        if dialect_name == "postgresql":
            return db.func.coalesce(column, "").ilike(pattern)
        return db.func.lower(db.func.coalesce(column, "")).like(pattern.lower())

    def _token_mode_clause(columns, token: str, mode: str):
        if mode == "exact":
            return db.or_(*[_like(col, token) for col in columns])
        if mode == "prefix":
            patterns = (f"{token}%", f"% {token}%", f"%-{token}%")
            return db.or_(*[_like(col, pattern) for col in columns for pattern in patterns])
        return db.or_(*[_like(col, f"%{token}%") for col in columns])

    def _filter_for_tokens(columns, *, mode: str):
        source_tokens = sql_tokens if mode in {"exact", "prefix", "contains"} else tokens
        return db.and_(*[_token_mode_clause(columns, token, mode) for token in source_tokens])

    def _run_ranked_rows(base_query, columns, order_by, *, row_id_attr="id"):
        selected = []
        seen_ids = set()
        for mode in ("exact", "prefix", "contains"):
            needed = fetch_limit - len(selected)
            if needed <= 0:
                break
            q = base_query.filter(_filter_for_tokens(columns, mode=mode))
            if seen_ids:
                q = q.filter(~getattr(base_query.column_descriptions[0]["entity"], row_id_attr).in_(seen_ids))
            rows = q.order_by(*order_by).limit(needed).all()
            for row in rows:
                row_id = getattr(row, row_id_attr, None)
                if row_id in seen_ids:
                    continue
                seen_ids.add(row_id)
                selected.append(row)
        return selected

    token_phrase = " ".join(tokens)

    def _score_item(label: str | None, meta: str | None, *extras: str | None) -> int:
        bag = " ".join([label or "", meta or "", *[x or "" for x in extras]])
        bag_low = _normalize_search_text(bag)
        label_low = _normalize_search_text(label)
        meta_low = _normalize_search_text(meta)
        score = 0
        if token_phrase and token_phrase in label_low:
            score += 110
        if token_phrase and token_phrase in bag_low:
            score += 35
        for token in tokens:
            if label_low == token:
                score += 180
            if label_low.startswith(token):
                score += 80
            if f" {token}" in label_low or f"-{token}" in label_low:
                score += 30
            if token in label_low:
                score += 40
            if token in meta_low:
                score += 16
            if token in bag_low:
                score += 12
        score += max(0, 30 - max(0, len(label_low) - len(token_phrase or label_low)))
        return score

    if (wanted_type in {None, "Participant"}) and (can("participants:view") or can("participants:view_all")):
        participants_q = Participant.query.outerjoin(Quartier, Quartier.id == Participant.quartier_id)
        if quartier_filter:
            participants_q = participants_q.filter(
                db.or_(
                    _like(Quartier.nom, f"%{quartier_filter}%"),
                    _like(Quartier.ville, f"%{quartier_filter}%"),
                )
            )
        if active_secteur_filter:
            has_presence_in_user_secteur = (
                db.session.query(PresenceActivite.id)
                .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
                .filter(PresenceActivite.participant_id == Participant.id)
                .filter(SessionActivite.secteur == active_secteur_filter)
                .exists()
            )
            participants_q = participants_q.filter(
                db.or_(
                    Participant.created_secteur == active_secteur_filter,
                    has_presence_in_user_secteur,
                )
            )
        rows = _run_ranked_rows(
            participants_q,
            [Participant.nom, Participant.prenom, Participant.email, Participant.telephone, Participant.ville, Participant.adresse, Participant.created_secteur, Quartier.nom, Quartier.ville],
            [Participant.updated_at.desc(), Participant.nom.asc(), Participant.prenom.asc()],
        )
        for row in rows:
            label = f"{(row.prenom or '').strip()} {(row.nom or '').strip()}".strip() or f"Participant #{row.id}"
            quartier_nom = (row.quartier.nom if row.quartier else "") or ""
            ville = (row.ville or (row.quartier.ville if row.quartier else "") or "").strip()
            meta_bits = [bit for bit in [quartier_nom, ville, row.email or row.telephone or "", row.created_secteur or ""] if bit]
            meta = " · ".join(meta_bits[:4]) or "Fiche participant"
            results.append({
                "type": "Participant",
                "label": label,
                "meta": meta,
                "secteur": row.created_secteur or "",
                "url": url_for("participants.edit_participant", participant_id=row.id),
                "score": _score_item(label, meta, row.adresse, quartier_nom, row.ville, row.email, row.telephone),
            })

    if (wanted_type in {None, "Quartier"}) and can("quartiers:view"):
        quartiers_q = Quartier.query
        rows = _run_ranked_rows(
            quartiers_q,
            [Quartier.nom, Quartier.ville, Quartier.description],
            [Quartier.ville.asc(), Quartier.nom.asc()],
        )
        for row in rows:
            if quartier_filter and quartier_filter not in _normalize_search_text(f"{row.nom} {row.ville} {row.description}"):
                continue
            label = row.nom
            meta_bits = [row.ville or "", "QPV" if row.is_qpv else "", row.description or ""]
            meta = " · ".join([bit for bit in meta_bits if bit][:3])
            results.append({
                "type": "Quartier",
                "label": label,
                "meta": meta,
                "secteur": "",
                "url": url_for("quartiers.edit", quartier_id=row.id),
                "score": _score_item(label, meta, row.description, row.ville),
            })

    if (wanted_type in {None, "Projet"}) and can("projets:view"):
        projets_q = Projet.query
        if active_secteur_filter:
            projets_q = projets_q.filter(Projet.secteur == active_secteur_filter)
        rows = _run_ranked_rows(
            projets_q,
            [Projet.nom, Projet.description, Projet.secteur],
            [Projet.created_at.desc(), Projet.nom.asc()],
        )
        for row in rows:
            label = row.nom
            meta = " · ".join([bit for bit in [row.secteur or "", (row.description or "")[:80].strip()] if bit][:2])
            results.append({
                "type": "Projet",
                "label": label,
                "meta": meta,
                "secteur": row.secteur or "",
                "url": url_for("projets.projets_edit", projet_id=row.id),
                "score": _score_item(label, meta, row.description, row.secteur),
            })

    if (wanted_type in {None, "Subvention"}) and can("subventions:view"):
        subventions_q = Subvention.query.filter(Subvention.est_archive.is_(False))
        if active_secteur_filter:
            subventions_q = subventions_q.filter(Subvention.secteur == active_secteur_filter)
        rows = _run_ranked_rows(
            subventions_q,
            [Subvention.nom, Subvention.secteur],
            [Subvention.annee_exercice.desc(), Subvention.created_at.desc(), Subvention.nom.asc()],
        )
        for row in rows:
            label = row.nom
            meta = " · ".join([bit for bit in [row.secteur or "", str(row.annee_exercice or "")] if bit])
            results.append({
                "type": "Subvention",
                "label": label,
                "meta": meta,
                "secteur": row.secteur or "",
                "url": url_for("main.subvention_pilotage", subvention_id=row.id),
                "score": _score_item(label, meta, row.secteur, str(row.annee_exercice or "")),
            })

    latest_session_subq = db.session.query(
        SessionActivite.atelier_id.label("atelier_id"),
        db.func.max(db.func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date)).label("last_session_date"),
    ).group_by(SessionActivite.atelier_id).subquery()

    if (wanted_type in {None, "Atelier"}) and can("emargement:view"):
        ateliers_q = AtelierActivite.query.outerjoin(latest_session_subq, latest_session_subq.c.atelier_id == AtelierActivite.id).filter(AtelierActivite.is_deleted.is_(False))
        if active_secteur_filter:
            ateliers_q = ateliers_q.filter(AtelierActivite.secteur == active_secteur_filter)
        rows = _run_ranked_rows(
            ateliers_q,
            [AtelierActivite.nom, AtelierActivite.description, AtelierActivite.secteur, AtelierActivite.type_atelier],
            [latest_session_subq.c.last_session_date.desc().nullslast(), AtelierActivite.nom.asc()],
        )
        for row in rows:
            last_date = None
            try:
                last_date = max((s.date_session or s.rdv_date) for s in (row.sessions or []) if (s.date_session or s.rdv_date))
            except Exception:
                last_date = None
            meta_bits = [row.secteur or "", row.type_atelier or "", f"Dernière séance {last_date.strftime('%d/%m/%Y')}" if last_date else ""]
            label = row.nom
            meta = " · ".join([bit for bit in meta_bits if bit])
            results.append({
                "type": "Atelier",
                "label": label,
                "meta": meta,
                "secteur": row.secteur or "",
                "url": url_for("activite.sessions", atelier_id=row.id),
                "score": _score_item(label, meta, row.description, row.secteur, row.type_atelier),
            })

    if (wanted_type in {None, "Partenaire"}) and can("partenaires:view"):
        partenaires_q = Partenaire.query
        rows = _run_ranked_rows(
            partenaires_q,
            [Partenaire.nom, Partenaire.contact_nom, Partenaire.contact_prenom, Partenaire.email_contact, Partenaire.email_general, Partenaire.tel_contact, Partenaire.tel_general, Partenaire.adresse, Partenaire.description],
            [Partenaire.nom.asc()],
        )
        for row in rows:
            contact = " ".join([x for x in [row.contact_prenom, row.contact_nom] if x]).strip()
            meta_bits = [contact, row.email_contact or row.email_general or "", row.tel_contact or row.tel_general or "", row.adresse or ""]
            label = row.nom
            meta = " · ".join([bit for bit in meta_bits if bit][:4])
            results.append({
                "type": "Partenaire",
                "label": label,
                "meta": meta,
                "secteur": "",
                "url": url_for("partenaires.edit", partenaire_id=row.id),
                "score": _score_item(label, meta, row.description, row.contact_nom, row.contact_prenom, row.adresse),
            })

    results_sorted = sorted(
        results,
        key=lambda item: (
            -int(item.get("score") or 0),
            SEARCH_TYPE_PRIORITY.get(item.get("type") or "", 99),
            (item.get("label") or "").lower(),
        ),
    )
    type_facets = {}
    secteur_facets = {}
    for row in results_sorted:
        t = row.get("type") or "Autres"
        type_facets[t] = int(type_facets.get(t, 0)) + 1
        s = (row.get("secteur") or "").strip()
        if s:
            secteur_facets[s] = int(secteur_facets.get(s, 0)) + 1

    duration_ms = int((perf_counter() - start_ts) * 1000)
    current_app.logger.info(
        "global_search q=%r type=%r secteur=%r quartier=%r tokens=%s count=%s duration_ms=%s db=%s",
        term,
        wanted_type,
        active_secteur_filter,
        quartier_filter,
        tokens,
        min(len(results_sorted), panel_limit),
        duration_ms,
        dialect_name,
    )
    trimmed = [{k: v for k, v in row.items() if k != "score"} for row in results_sorted[:panel_limit]]
    full_rows = [{k: v for k, v in row.items() if k != "score"} for row in results_sorted[:page_limit]]
    return {
        "results": trimmed,
        "all_results": full_rows,
        "query": term,
        "debug": {"duration_ms": duration_ms},
        "facets": {"types": type_facets, "secteurs": secteur_facets},
        "meta": {
            "requested_type": wanted_type,
            "requested_secteur": active_secteur_filter,
            "requested_quartier": parsed["quartier_filter"] or "",
        },
    }


@bp.route("/api/global-search")
@login_required
def api_global_search():
    term = (request.args.get("q") or "").strip()
    payload = _run_global_search(term, panel_limit=20, page_limit=100)
    return jsonify({
        "results": payload.get("results", []),
        "query": payload.get("query", term),
        "debug": payload.get("debug", {}),
        "facets": payload.get("facets", {}),
        "meta": payload.get("meta", {}),
    })


@bp.route("/recherche")
@login_required
def global_search_results():
    term = (request.args.get("q") or "").strip()
    selected_type = (request.args.get("type") or "").strip()
    effective_term = term
    if selected_type and f"type:{selected_type.lower()}" not in term.lower():
        effective_term = f"type:{selected_type} {term}".strip()

    payload = _run_global_search(effective_term, panel_limit=100, page_limit=100)
    results = payload.get("all_results") or payload.get("results") or []
    if selected_type:
        results = [item for item in results if (item.get("type") or "").lower() == selected_type.lower()]

    grouped_results = {}
    for item in results:
        group_key = (item.get("type") or "Autres").strip() or "Autres"
        grouped_results.setdefault(group_key, []).append(item)

    context = {
        "query": payload.get("query", term),
        "results": results,
        "items": results,
        "grouped_results": grouped_results,
        "facets": payload.get("facets") if isinstance(payload.get("facets"), dict) else {"types": {}, "secteurs": {}},
        "debug": payload.get("debug") if isinstance(payload.get("debug"), dict) else {},
        "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        "selected_type": selected_type,
    }
    try:
        return render_template("search_results.html", **context)
    except Exception:
        current_app.logger.exception("global_search_results render failed for q=%r", term)
        return render_template("search_results_fallback.html", **context), 200



