from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import func

from app.models import (
    MaterielType,
    MaterielConsommationConfig,
    SessionMateriel,
    PresenceActivite,
    PresenceMaterielConsommation,
    SessionActivite,
    AtelierActivite,
    ProjetAtelier,
)
from app.extensions import db


def list_materiels_actifs():
    return MaterielType.query.filter_by(actif=True).order_by(MaterielType.ordre.asc(), MaterielType.nom.asc()).all()


def active_config_for_date(target_date: date | None):
    if not target_date:
        return None
    return (
        MaterielConsommationConfig.query
        .filter(MaterielConsommationConfig.actif.is_(True))
        .filter(MaterielConsommationConfig.date_debut <= target_date)
        .filter((MaterielConsommationConfig.date_fin.is_(None)) | (MaterielConsommationConfig.date_fin >= target_date))
        .order_by(MaterielConsommationConfig.date_debut.desc(), MaterielConsommationConfig.id.desc())
        .first()
    )


def _session_date(session):
    return getattr(session, 'rdv_date', None) or getattr(session, 'date_session', None)




CONSUMPTION_PERIOD_MODES = {
    'civil_year': 'Année civile',
    'school_year': 'Année scolaire',
    'calendar_quarter': 'Trimestre civil',
    'rolling_3_months': '3 derniers mois',
    'custom': 'Période personnalisée',
}


def _add_months(d: date, months: int) -> date:
    """Ajoute/soustrait des mois sans dépendance externe, en bornant le jour."""
    year = d.year + ((d.month - 1 + months) // 12)
    month = ((d.month - 1 + months) % 12) + 1
    # jours par mois, février géré grossièrement par construction date avec fallback.
    for day in range(d.day, 27, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 1)


def _coerce_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


def resolve_consumption_period(
    session,
    mode: str | None = None,
    period_start=None,
    period_end=None,
) -> dict:
    """Résout la période de cumul individuel à partir de la date de séance.

    Le cumul est volontairement plafonné à la date de la séance : une feuille
    d'émargement ne doit pas afficher des consommations futures si elle est
    régénérée plus tard.
    """
    current = _session_date(session)
    if not current:
        return {
            'mode': mode or 'civil_year',
            'start': None,
            'end': None,
            'label': 'période non définie',
            'modes': CONSUMPTION_PERIOD_MODES,
        }

    mode = (mode or 'civil_year').strip() or 'civil_year'
    if mode not in CONSUMPTION_PERIOD_MODES:
        mode = 'civil_year'

    custom_start = _coerce_date(period_start)
    custom_end = _coerce_date(period_end)

    if mode == 'school_year':
        start_year = current.year if current.month >= 9 else current.year - 1
        start = date(start_year, 9, 1)
        label = f"année scolaire {start_year}-{start_year + 1}"
    elif mode == 'calendar_quarter':
        quarter_start_month = (((current.month - 1) // 3) * 3) + 1
        start = date(current.year, quarter_start_month, 1)
        quarter = ((current.month - 1) // 3) + 1
        label = f"T{quarter} {current.year}"
    elif mode == 'rolling_3_months':
        start = _add_months(current, -3) + timedelta(days=1)
        label = '3 derniers mois'
    elif mode == 'custom':
        start = custom_start or date(current.year, 1, 1)
        label = 'période personnalisée'
    else:
        start = date(current.year, 1, 1)
        label = f"année civile {current.year}"

    # Pour les documents de séance, on ne va jamais au-delà de la date de séance.
    end = custom_end if mode == 'custom' and custom_end else current
    if end > current:
        end = current
    if start > end:
        start, end = end, start
        if end > current:
            end = current

    return {
        'mode': mode,
        'start': start,
        'end': end,
        'label': label,
        'modes': CONSUMPTION_PERIOD_MODES,
    }


def _parse_hhmm(value: str | None) -> int | None:
    if not value or ':' not in value:
        return None
    try:
        h, m = [int(x) for x in value.split(':', 1)]
        return h * 60 + m
    except Exception:
        return None


def session_duration_minutes(session) -> int:
    """Durée de séance robuste, utilisée pour les calculs globaux et individuels."""
    duration_minutes = getattr(session, 'duree_minutes', None)
    if duration_minutes:
        try:
            return max(0, int(duration_minutes)) or 60
        except Exception:
            return 60

    start = getattr(session, 'rdv_debut', None) or getattr(session, 'heure_debut', None)
    end = getattr(session, 'rdv_fin', None) or getattr(session, 'heure_fin', None)
    start_min = _parse_hhmm(start)
    end_min = _parse_hhmm(end)
    if start_min is not None and end_min is not None:
        diff = end_min - start_min
        # Tolérance pour les cas rarissimes qui débordent après minuit.
        if diff < 0:
            diff += 24 * 60
        return max(0, diff) or 60

    return 60


def assign_session_config(session):
    target_date = _session_date(session)
    cfg = active_config_for_date(target_date)
    if cfg:
        session.consommation_config_id = cfg.id
    return cfg


def save_session_materiels_from_form(session, form):
    active_ids = {m.id for m in list_materiels_actifs()}
    existing = {sm.materiel_id: sm for sm in getattr(session, 'materiels', [])}
    for materiel_id in active_ids:
        raw = (form.get(f'quantite_{materiel_id}') or '').strip()
        try:
            qty = int(raw or '0')
        except Exception:
            qty = 0
        sm = existing.get(materiel_id)
        if qty > 0:
            if sm:
                sm.quantite = qty
            else:
                db.session.add(SessionMateriel(session_id=session.id, materiel_id=materiel_id, quantite=qty))
        elif sm:
            db.session.delete(sm)


def _config_for_session(session):
    target_date = _session_date(session)
    return getattr(session, 'consommation_config', None) or active_config_for_date(target_date)


def _watt_map(cfg) -> dict[int, float]:
    if not cfg:
        return {}
    return {line.materiel_id: float(line.watts or 0) for line in cfg.lignes}


def calculate_session_consumption(session, atelier=None):
    target_date = _session_date(session)
    cfg = getattr(session, 'consommation_config', None) or active_config_for_date(target_date)
    if not cfg:
        return {'config': None, 'total_kwh': 0.0, 'co2_kg': 0.0, 'details': [], 'materiels_count': 0}

    duration_minutes = session_duration_minutes(session)
    duration_hours = (duration_minutes or 60) / 60.0
    watt_map = {line.materiel_id: float(line.watts or 0) for line in cfg.lignes}
    details = []
    total = 0.0
    for sm in getattr(session, 'materiels', []) or []:
        watts = watt_map.get(sm.materiel_id, 0.0)
        if watts <= 0 or (sm.quantite or 0) <= 0:
            continue
        kwh = (sm.quantite * watts * duration_hours) / 1000.0
        total += kwh
        details.append({
            'nom': sm.materiel.nom if sm.materiel else f'Matériel #{sm.materiel_id}',
            'materiel_id': sm.materiel_id,
            'quantite': sm.quantite,
            'watts': watts,
            'kwh': round(kwh, 3),
        })
    co2 = total * float(cfg.co2_kg_par_kwh or 0.0)
    return {
        'config': cfg,
        'total_kwh': round(total, 3),
        'co2_kg': round(co2, 3),
        'details': details,
        'materiels_count': sum(int(d['quantite']) for d in details),
    }


def default_individual_materiel_id(session) -> int | None:
    """Premier matériel déclaré sur la séance, utilisé comme choix rapide individuel."""
    rows = sorted(
        [sm for sm in (getattr(session, 'materiels', []) or []) if (sm.quantite or 0) > 0],
        key=lambda sm: (getattr(getattr(sm, 'materiel', None), 'ordre', 0), getattr(getattr(sm, 'materiel', None), 'nom', ''), sm.id or 0),
    )
    return rows[0].materiel_id if rows else None


def upsert_presence_consumption(
    presence: PresenceActivite,
    materiel_id: int | None,
    quantite: int = 1,
    mode_calcul: str = 'manuel',
    commit: bool = False,
):
    """Crée/met à jour la conso individuelle figée d'une présence.

    materiel_id=None ou quantite<=0 supprime les lignes de calcul manuel/auto de la présence.
    """
    quantite = int(quantite or 0)
    mode_calcul = (mode_calcul or 'manuel')[:40]

    if not presence or not getattr(presence, 'session', None):
        return None

    if not materiel_id or quantite <= 0:
        PresenceMaterielConsommation.query.filter_by(presence_id=presence.id).delete()
        if commit:
            db.session.commit()
        return None

    session = presence.session
    cfg = _config_for_session(session) or assign_session_config(session)
    materiel = db.session.get(MaterielType, materiel_id)
    duration_minutes = session_duration_minutes(session)
    watts = 0.0
    co2_factor = 0.06
    if cfg:
        watts = _watt_map(cfg).get(int(materiel_id), 0.0)
        co2_factor = float(cfg.co2_kg_par_kwh or 0.0)
    kwh = (quantite * watts * (duration_minutes / 60.0)) / 1000.0
    co2 = kwh * co2_factor

    # MVP : une seule ligne de conso individuelle par présence. Si on change le matériel,
    # on remplace l'ancienne ligne pour éviter les doublons invisibles dans les feuilles.
    existing_rows = PresenceMaterielConsommation.query.filter_by(presence_id=presence.id).all()
    row = None
    for candidate in existing_rows:
        if candidate.materiel_id == materiel_id and candidate.mode_calcul == mode_calcul:
            row = candidate
        else:
            db.session.delete(candidate)

    if row is None:
        row = PresenceMaterielConsommation(
            presence_id=presence.id,
            session_id=presence.session_id,
            participant_id=presence.participant_id,
            materiel_id=materiel_id,
            mode_calcul=mode_calcul,
        )
        db.session.add(row)

    row.session_id = presence.session_id
    row.participant_id = presence.participant_id
    row.materiel_id = materiel_id
    row.quantite = quantite
    row.materiel_nom_snapshot = materiel.nom if materiel else None
    row.watts_snapshot = round(float(watts or 0.0), 3)
    row.duree_minutes_snapshot = int(duration_minutes or 60)
    row.kwh_snapshot = round(float(kwh or 0.0), 6)
    row.co2_kg_snapshot = round(float(co2 or 0.0), 6)
    row.co2_kg_par_kwh_snapshot = round(float(co2_factor or 0.0), 6)
    row.mode_calcul = mode_calcul

    if commit:
        db.session.commit()
    return row



def replace_presence_consumptions(
    presence: PresenceActivite,
    materiel_ids: Iterable[int] | None,
    quantite: int = 1,
    mode_calcul: str = 'manuel',
    commit: bool = False,
) -> list[PresenceMaterielConsommation]:
    """Remplace toute la conso individuelle d'une présence par plusieurs matériels.

    Cette fonction est utilisée par l'émargement quand un usager utilise plusieurs
    équipements pendant la même séance, par exemple tour fixe + écran, ou portable
    + imprimante. Chaque matériel produit sa propre ligne figée, puis les résumés
    et cumuls additionnent les lignes.

    Une quantité unique est appliquée à chaque matériel sélectionné. Dans la plupart
    des cas métier, cela correspond à 1 tour + 1 écran + 1 imprimante.
    """
    if not presence or not getattr(presence, 'session', None):
        return []

    try:
        quantite = max(0, int(quantite or 0))
    except Exception:
        quantite = 1
    mode_calcul = (mode_calcul or 'manuel')[:40]

    clean_ids: list[int] = []
    seen: set[int] = set()
    for raw in materiel_ids or []:
        try:
            mid = int(raw)
        except Exception:
            continue
        if mid > 0 and mid not in seen:
            clean_ids.append(mid)
            seen.add(mid)

    # Remplacement complet : ce qui n'est plus sélectionné disparaît.
    PresenceMaterielConsommation.query.filter_by(presence_id=presence.id).delete(synchronize_session=False)

    if not clean_ids or quantite <= 0:
        if commit:
            db.session.commit()
        return []

    session = presence.session
    cfg = _config_for_session(session) or assign_session_config(session)
    watt_map = _watt_map(cfg)
    duration_minutes = session_duration_minutes(session)
    duration_hours = (duration_minutes or 60) / 60.0
    co2_factor = float(getattr(cfg, 'co2_kg_par_kwh', 0.06) or 0.0) if cfg else 0.06

    rows: list[PresenceMaterielConsommation] = []
    for materiel_id in clean_ids:
        materiel = db.session.get(MaterielType, materiel_id)
        watts = float(watt_map.get(int(materiel_id), 0.0) or 0.0)
        kwh = (quantite * watts * duration_hours) / 1000.0
        co2 = kwh * co2_factor
        row = PresenceMaterielConsommation(
            presence_id=presence.id,
            session_id=presence.session_id,
            participant_id=presence.participant_id,
            materiel_id=materiel_id,
            quantite=quantite,
            materiel_nom_snapshot=materiel.nom if materiel else None,
            watts_snapshot=round(float(watts or 0.0), 3),
            duree_minutes_snapshot=int(duration_minutes or 60),
            kwh_snapshot=round(float(kwh or 0.0), 6),
            co2_kg_snapshot=round(float(co2 or 0.0), 6),
            co2_kg_par_kwh_snapshot=round(float(co2_factor or 0.0), 6),
            mode_calcul=mode_calcul,
        )
        db.session.add(row)
        rows.append(row)

    if commit:
        db.session.commit()
    return rows


def ensure_presence_consumption_from_session_default(presence: PresenceActivite, commit: bool = False):
    materiel_id = default_individual_materiel_id(presence.session)
    if not materiel_id:
        return None
    return upsert_presence_consumption(presence, materiel_id=materiel_id, quantite=1, mode_calcul='auto_session_default', commit=commit)


def regenerate_presence_consumptions_for_session(
    session,
    materiel_id: int | None = None,
    materiel_ids: Iterable[int] | None = None,
    quantite: int = 1,
    commit: bool = False,
) -> int:
    """Applique un ou plusieurs matériels individuels à tous les présents de la séance."""
    selected_ids: list[int] = []
    if materiel_ids:
        seen: set[int] = set()
        for raw in materiel_ids:
            try:
                mid = int(raw)
            except Exception:
                continue
            if mid > 0 and mid not in seen:
                selected_ids.append(mid)
                seen.add(mid)
    elif materiel_id:
        selected_ids = [int(materiel_id)]
    else:
        default_id = default_individual_materiel_id(session)
        selected_ids = [int(default_id)] if default_id else []

    if not selected_ids:
        return 0

    count = 0
    presences = PresenceActivite.query.filter_by(session_id=session.id).all()
    for pr in presences:
        replace_presence_consumptions(pr, materiel_ids=selected_ids, quantite=quantite, mode_calcul='auto_session_default')
        count += 1
    if commit:
        db.session.commit()
    return count


def clear_presence_consumptions_for_session(session, commit: bool = False) -> int:
    q = PresenceMaterielConsommation.query.filter_by(session_id=session.id)
    count = q.count()
    q.delete(synchronize_session=False)
    if commit:
        db.session.commit()
    return count


def session_consumption_period(session, mode: str | None = None, period_start=None, period_end=None) -> tuple[date | None, date | None]:
    """Retourne la période de cumul à utiliser pour une séance."""
    ctx = resolve_consumption_period(session, mode=mode, period_start=period_start, period_end=period_end)
    return ctx.get('start'), ctx.get('end')


def presence_consumption_summary(presence: PresenceActivite) -> dict:
    rows = list(getattr(presence, 'consommations_materiel', None) or [])
    rows.sort(key=lambda row: ((row.materiel_nom_snapshot or (row.materiel.nom if row.materiel else '') or '').lower(), row.id or 0))
    kwh = sum(float(row.kwh_snapshot or 0.0) for row in rows)
    co2 = sum(float(row.co2_kg_snapshot or 0.0) for row in rows)
    materiels = []
    materiel_ids: list[int] = []
    quantities: list[int] = []
    details = []
    first_row = rows[0] if rows else None
    for row in rows:
        label = row.materiel_nom_snapshot or (row.materiel.nom if row.materiel else '') or 'Matériel'
        qty = int(row.quantite or 0)
        if row.materiel_id:
            materiel_ids.append(int(row.materiel_id))
        quantities.append(qty or 1)
        materiels.append(f"{qty}× {label}" if qty and qty != 1 else label)
        details.append({
            'materiel_id': int(row.materiel_id) if row.materiel_id else None,
            'nom': label,
            'quantite': qty or 1,
            'kwh': round(float(row.kwh_snapshot or 0.0), 3),
            'co2_kg': round(float(row.co2_kg_snapshot or 0.0), 3),
        })
    common_quantite = quantities[0] if quantities and all(q == quantities[0] for q in quantities) else 1
    return {
        'kwh': round(kwh, 3),
        'co2_kg': round(co2, 3),
        'materiels': ', '.join([m for m in materiels if m]) or '—',
        'materiel_id': int(first_row.materiel_id) if first_row and first_row.materiel_id else None,
        'materiel_ids': materiel_ids,
        'quantite': common_quantite,
        'details': details,
        'has_rows': bool(rows),
    }


def participant_consumption_cumuls_for_session(session, period_mode: str | None = None, period_start=None, period_end=None) -> dict[int, dict]:
    """Cumul individuel par participant sur la période choisie, jusqu'à la séance incluse."""
    ctx = resolve_consumption_period(session, mode=period_mode, period_start=period_start, period_end=period_end)
    start, end = ctx.get('start'), ctx.get('end')
    if not start or not end:
        return {}

    date_expr = func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)
    rows = (
        db.session.query(
            PresenceMaterielConsommation.participant_id,
            func.sum(PresenceMaterielConsommation.kwh_snapshot),
            func.sum(PresenceMaterielConsommation.co2_kg_snapshot),
        )
        .join(SessionActivite, SessionActivite.id == PresenceMaterielConsommation.session_id)
        .filter(SessionActivite.is_deleted.is_(False))
        .filter(date_expr >= start)
        .filter(date_expr <= end)
        .group_by(PresenceMaterielConsommation.participant_id)
        .all()
    )
    return {
        int(participant_id): {
            'kwh': round(float(kwh or 0.0), 3),
            'co2_kg': round(float(co2 or 0.0), 3),
            'period_start': start,
            'period_end': end,
            'period_label': ctx.get('label'),
            'period_mode': ctx.get('mode'),
        }
        for participant_id, kwh, co2 in rows
    }


def build_presence_consumption_maps(
    presences: Iterable[PresenceActivite],
    session,
    period_mode: str | None = None,
    period_start=None,
    period_end=None,
) -> tuple[dict[int, dict], dict[int, dict]]:
    """Retourne (conso séance par présence, cumul période par participant)."""
    per_presence = {pr.id: presence_consumption_summary(pr) for pr in presences}
    cumuls = participant_consumption_cumuls_for_session(
        session,
        period_mode=period_mode,
        period_start=period_start,
        period_end=period_end,
    )
    return per_presence, cumuls



def _session_date_expr_for_consumption():
    return func.coalesce(SessionActivite.rdv_date, SessionActivite.date_session)


def _as_int_list(values) -> list[int]:
    if values in (None, "", "None"):
        return []
    raw_items = values if isinstance(values, (list, tuple, set)) else [values]
    out: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        if item in (None, "", "None"):
            continue
        if isinstance(item, str) and "," in item:
            chunks = [x.strip() for x in item.split(",")]
        else:
            chunks = [item]
        for chunk in chunks:
            try:
                val = int(chunk)
            except Exception:
                continue
            if val > 0 and val not in seen:
                out.append(val)
                seen.add(val)
    return out


def _as_str_list(values) -> list[str]:
    if values is None:
        return []
    raw_items = values if isinstance(values, (list, tuple, set)) else [values]
    out: list[str] = []
    for item in raw_items:
        val = str(item or '').strip()
        if val:
            out.append(val)
    return out


def aggregate_individual_consumption(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    secteur: str | None = None,
    secteurs=None,
    atelier_ids=None,
    projet_id: int | None = None,
    include_inactive_ateliers: bool = True,
    top_limit: int = 8,
) -> dict:
    """Agrège la consommation individuelle enregistrée sur les présences.

    Contrairement à ``aggregate_sessions_consumption``, cette fonction lit les
    lignes figées ``presence_materiel_consommation``. Elle correspond donc au
    suivi réel par habitant : une présence peut avoir zéro, un ou plusieurs
    matériels, avec des valeurs snapshot qui ne bougent pas si le référentiel
    matériel évolue plus tard.
    """
    selected_ateliers = set(_as_int_list(atelier_ids))

    if projet_id:
        linked_ids = {
            int(row[0])
            for row in db.session.query(ProjetAtelier.atelier_id)
            .filter(ProjetAtelier.projet_id == int(projet_id))
            .all()
            if row and row[0]
        }
        selected_ateliers = (selected_ateliers & linked_ids) if selected_ateliers else linked_ids

    if projet_id and not selected_ateliers:
        return {
            'total_kwh': 0.0,
            'total_co2': 0.0,
            'sessions_count': 0,
            'presences_count': 0,
            'participants_count': 0,
            'materiel_lines_count': 0,
            'avg_kwh_per_presence': 0.0,
            'top_materiels': [],
            'by_atelier': [],
            'by_secteur': [],
        }

    if secteur and not secteurs:
        secteurs = [secteur]
    secteur_list = _as_str_list(secteurs)
    if secteurs is not None and not secteur_list:
        return {
            'total_kwh': 0.0,
            'total_co2': 0.0,
            'sessions_count': 0,
            'presences_count': 0,
            'participants_count': 0,
            'materiel_lines_count': 0,
            'avg_kwh_per_presence': 0.0,
            'top_materiels': [],
            'by_atelier': [],
            'by_secteur': [],
        }

    date_expr = _session_date_expr_for_consumption()

    def base_query():
        q = (
            db.session.query(PresenceMaterielConsommation)
            .join(SessionActivite, SessionActivite.id == PresenceMaterielConsommation.session_id)
            .join(AtelierActivite, AtelierActivite.id == SessionActivite.atelier_id)
            .filter(SessionActivite.is_deleted.is_(False))
            .filter(AtelierActivite.is_deleted.is_(False))
            .filter(func.lower(func.coalesce(SessionActivite.statut, '')) != 'annulee')
        )
        if not include_inactive_ateliers:
            q = q.filter(AtelierActivite.is_active.is_(True))
        if secteur_list:
            q = q.filter(AtelierActivite.secteur.in_(secteur_list))
        if selected_ateliers:
            q = q.filter(AtelierActivite.id.in_(selected_ateliers))
        if date_from:
            q = q.filter(date_expr >= date_from)
        if date_to:
            q = q.filter(date_expr <= date_to)
        return q

    total_row = (
        base_query()
        .with_entities(
            func.coalesce(func.sum(PresenceMaterielConsommation.kwh_snapshot), 0.0),
            func.coalesce(func.sum(PresenceMaterielConsommation.co2_kg_snapshot), 0.0),
            func.count(PresenceMaterielConsommation.id),
            func.count(func.distinct(PresenceMaterielConsommation.presence_id)),
            func.count(func.distinct(PresenceMaterielConsommation.participant_id)),
            func.count(func.distinct(PresenceMaterielConsommation.session_id)),
        )
        .first()
    )

    total_kwh = float(total_row[0] or 0.0) if total_row else 0.0
    total_co2 = float(total_row[1] or 0.0) if total_row else 0.0
    lines_count = int(total_row[2] or 0) if total_row else 0
    presences_count = int(total_row[3] or 0) if total_row else 0
    participants_count = int(total_row[4] or 0) if total_row else 0
    sessions_count = int(total_row[5] or 0) if total_row else 0

    top_rows = (
        base_query()
        .with_entities(
            func.coalesce(PresenceMaterielConsommation.materiel_nom_snapshot, 'Matériel non renseigné'),
            func.coalesce(func.sum(PresenceMaterielConsommation.kwh_snapshot), 0.0),
            func.count(PresenceMaterielConsommation.id),
        )
        # PostgreSQL n'aime pas toujours grouper sur un COALESCE contenant
        # une valeur bindée si le même COALESCE est aussi utilisé dans le SELECT :
        # SQLAlchemy peut générer deux paramètres différents, et Postgres ne
        # reconnaît alors plus l'expression comme identique. Grouper sur la
        # colonne brute règle le problème tout en gardant le libellé de secours
        # dans le SELECT.
        .group_by(PresenceMaterielConsommation.materiel_nom_snapshot)
        .order_by(func.sum(PresenceMaterielConsommation.kwh_snapshot).desc())
        .limit(int(top_limit or 8))
        .all()
    )

    by_atelier_rows = (
        base_query()
        .with_entities(
            AtelierActivite.id,
            AtelierActivite.secteur,
            AtelierActivite.nom,
            func.coalesce(func.sum(PresenceMaterielConsommation.kwh_snapshot), 0.0),
            func.coalesce(func.sum(PresenceMaterielConsommation.co2_kg_snapshot), 0.0),
            func.count(func.distinct(PresenceMaterielConsommation.presence_id)),
            func.count(func.distinct(PresenceMaterielConsommation.participant_id)),
            func.count(func.distinct(PresenceMaterielConsommation.session_id)),
        )
        .group_by(AtelierActivite.id, AtelierActivite.secteur, AtelierActivite.nom)
        .order_by(func.sum(PresenceMaterielConsommation.kwh_snapshot).desc())
        .all()
    )

    by_secteur_rows = (
        base_query()
        .with_entities(
            AtelierActivite.secteur,
            func.coalesce(func.sum(PresenceMaterielConsommation.kwh_snapshot), 0.0),
            func.coalesce(func.sum(PresenceMaterielConsommation.co2_kg_snapshot), 0.0),
            func.count(func.distinct(PresenceMaterielConsommation.presence_id)),
            func.count(func.distinct(PresenceMaterielConsommation.participant_id)),
            func.count(func.distinct(PresenceMaterielConsommation.session_id)),
        )
        .group_by(AtelierActivite.secteur)
        .order_by(func.sum(PresenceMaterielConsommation.kwh_snapshot).desc())
        .all()
    )

    return {
        'total_kwh': round(total_kwh, 3),
        'total_co2': round(total_co2, 3),
        'sessions_count': sessions_count,
        'presences_count': presences_count,
        'participants_count': participants_count,
        'materiel_lines_count': lines_count,
        'avg_kwh_per_presence': round(total_kwh / presences_count, 3) if presences_count else 0.0,
        'top_materiels': [
            {'nom': name, 'kwh': round(float(kwh or 0.0), 3), 'lignes': int(count or 0)}
            for name, kwh, count in top_rows
        ],
        'by_atelier': [
            {
                'atelier_id': int(aid),
                'secteur': sec or '—',
                'nom': nom or '—',
                'kwh': round(float(kwh or 0.0), 3),
                'co2_kg': round(float(co2 or 0.0), 3),
                'presences_count': int(pres or 0),
                'participants_count': int(parts or 0),
                'sessions_count': int(sess or 0),
            }
            for aid, sec, nom, kwh, co2, pres, parts, sess in by_atelier_rows
        ],
        'by_secteur': [
            {
                'secteur': sec or '—',
                'kwh': round(float(kwh or 0.0), 3),
                'co2_kg': round(float(co2 or 0.0), 3),
                'presences_count': int(pres or 0),
                'participants_count': int(parts or 0),
                'sessions_count': int(sess or 0),
            }
            for sec, kwh, co2, pres, parts, sess in by_secteur_rows
        ],
    }

def aggregate_sessions_consumption(sessions):
    total_kwh = 0.0
    total_co2 = 0.0
    session_count = 0
    by_materiel = defaultdict(float)
    for s in sessions:
        payload = calculate_session_consumption(s)
        total_kwh += payload['total_kwh']
        total_co2 += payload['co2_kg']
        if payload['details']:
            session_count += 1
        for d in payload['details']:
            by_materiel[d['nom']] += d['kwh']
    top = sorted(by_materiel.items(), key=lambda x: x[1], reverse=True)
    return {
        'total_kwh': round(total_kwh, 2),
        'total_co2': round(total_co2, 2),
        'sessions_count': session_count,
        'avg_kwh_per_session': round(total_kwh / session_count, 3) if session_count else 0.0,
        'top_materiels': [{'nom': n, 'kwh': round(v, 2)} for n, v in top[:5]],
    }
