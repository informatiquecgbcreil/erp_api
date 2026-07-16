"""Saisie des présences en grille + suivi des feuilles d'émargement en retard.

Pensé pour le poste d'accueil : les feuilles papier arrivent au
compte-goutte (parfois avec un mois de retard) et se saisissent en masse,
a posteriori. La grille reproduit la gestuelle du tableur (participants en
lignes, dates en colonnes, cases à cocher) mais alimente la vraie base :
les statistiques et bilans se mettent à jour tout seuls.

Garde-fous :
- une présence signée (kiosque) ou avec un statut particulier (retard,
  absent excusé) n'est JAMAIS modifiée par la grille : la case est
  affichée verrouillée ;
- décocher ne supprime que des présences « simples » (sans signature).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.activite import bp
from app.activite.helpers import (
    _can_access_activity_secteur,
    _atelier_est_accessible,
    _session_est_accessible,
    _has_all_secteurs_scope,
    _is_admin_global,
    _require_any_perm,
    _session_effective_date_expr,
    _user_secteur,
)
from app.extensions import db
from app.models import (
    AtelierActivite,
    Participant,
    PresenceActivite,
    SessionActivite,
    SuiviRappel,
)
from app.rbac import require_perm
from app.services.poste_travail import MOIS_FR


def _ateliers_visibles():
    secteur = _user_secteur()
    if secteur:
        q = AtelierActivite.query.filter(db.or_(
            AtelierActivite.secteur == secteur,
            AtelierActivite.est_intersecteur.is_(True),
        ))
    elif _is_admin_global() or _has_all_secteurs_scope():
        q = AtelierActivite.query
    else:
        q = AtelierActivite.query.filter(db.false())
    return q.filter(AtelierActivite.is_deleted.is_(False)).order_by(AtelierActivite.nom.asc()).all()


def _mois_arg() -> tuple[int, int]:
    raw = (request.args.get("mois") or request.form.get("mois") or "").strip()
    try:
        annee, mois = raw.split("-", 1)
        return int(annee), int(mois)
    except Exception:
        today = date.today()
        return today.year, today.month


def _seances_du_mois(atelier_id: int, annee: int, mois: int) -> list[SessionActivite]:
    debut = date(annee, mois, 1)
    fin = date(annee, mois, calendar.monthrange(annee, mois)[1])
    eff = _session_effective_date_expr()
    return (
        SessionActivite.query
        .filter(
            SessionActivite.atelier_id == atelier_id,
            SessionActivite.is_deleted.is_(False),
            db.func.lower(db.func.coalesce(SessionActivite.statut, "")) != "annulee",
            eff >= debut,
            eff <= fin,
        )
        .order_by(eff.asc(), SessionActivite.id.asc())
        .all()
    )


def _participants_de_l_atelier(atelier_id: int, en_plus: list[int]) -> list[Participant]:
    """Les habitués (≥ 1 présence dans l'atelier) + les ajouts manuels."""
    ids = {
        pid
        for (pid,) in (
            db.session.query(PresenceActivite.participant_id)
            .join(SessionActivite, PresenceActivite.session_id == SessionActivite.id)
            .filter(SessionActivite.atelier_id == atelier_id)
            .distinct()
            .all()
        )
    }
    ids.update(en_plus)
    if not ids:
        return []
    return (
        Participant.query.filter(Participant.id.in_(ids))
        .order_by(Participant.nom.asc(), Participant.prenom.asc())
        .all()
    )


def _ids_en_plus() -> list[int]:
    raw = (request.args.get("plus") or "").strip()
    out = []
    for morceau in raw.split(","):
        try:
            out.append(int(morceau))
        except Exception:
            continue
    return out


@bp.route("/saisie-grille", methods=["GET", "POST"])
@login_required
def saisie_grille():
    """La grille : participants en lignes, séances du mois en colonnes."""
    _require_any_perm("emargement:view", "emargement:edit")

    ateliers = _ateliers_visibles()
    annee, mois = _mois_arg()
    mois_str = f"{annee:04d}-{mois:02d}"
    try:
        atelier_id = int(request.args.get("atelier_id") or request.form.get("atelier_id") or 0)
    except Exception:
        atelier_id = 0
    atelier = next((a for a in ateliers if a.id == atelier_id), None)
    en_plus = _ids_en_plus()

    if request.method == "POST":
        require_perm("emargement:edit")(lambda: None)()
        if atelier is None or not _atelier_est_accessible(atelier):
            flash("Atelier introuvable ou hors de ta portée.", "danger")
            return redirect(url_for("activite.saisie_grille", mois=mois_str))

        action = (request.form.get("action") or "").strip()

        if action == "add_session":
            d = (request.form.get("date_session") or "").strip()
            try:
                date_seance = datetime.strptime(d, "%Y-%m-%d").date()
            except Exception:
                flash("Date de séance invalide.", "danger")
                return redirect(url_for("activite.saisie_grille", atelier_id=atelier.id, mois=mois_str))
            s = SessionActivite(
                atelier_id=atelier.id,
                secteur=atelier.secteur,
                session_type="COLLECTIF",
                date_session=date_seance,
                heure_debut=(request.form.get("heure_debut") or "").strip() or None,
                heure_fin=(request.form.get("heure_fin") or "").strip() or None,
            )
            db.session.add(s)
            db.session.commit()
            flash(f"Séance du {date_seance.strftime('%d/%m/%Y')} créée.", "success")
            return redirect(url_for(
                "activite.saisie_grille", atelier_id=atelier.id,
                mois=f"{date_seance.year:04d}-{date_seance.month:02d}",
                plus=",".join(str(i) for i in en_plus) or None,
            ))

        if action == "add_participant":
            try:
                pid = int(request.form.get("participant_id") or 0)
            except Exception:
                pid = 0
            if pid and db.session.get(Participant, pid) is not None:
                if pid not in en_plus:
                    en_plus.append(pid)
            else:
                flash("Choisis une personne dans la liste.", "warning")
            return redirect(url_for(
                "activite.saisie_grille", atelier_id=atelier.id, mois=mois_str,
                plus=",".join(str(i) for i in en_plus),
            ))

        # --- Enregistrement de la grille ---
        seances = _seances_du_mois(atelier.id, annee, mois)
        ids_seances = {s.id for s in seances}
        paires = (request.form.get("paires") or "").split()
        existantes = {
            (p.participant_id, p.session_id): p
            for p in PresenceActivite.query.filter(PresenceActivite.session_id.in_(ids_seances)).all()
        } if ids_seances else {}

        ajoutees = retirees = protegees = 0
        for paire in paires:
            try:
                pid_str, sid_str = paire.split(":", 1)
                pid, sid = int(pid_str), int(sid_str)
            except Exception:
                continue
            if sid not in ids_seances:
                continue
            cochee = request.form.get(f"cell_{pid}_{sid}") == "1"
            presence = existantes.get((pid, sid))
            if cochee and presence is None:
                db.session.add(PresenceActivite(session_id=sid, participant_id=pid, presence_type="present"))
                ajoutees += 1
            elif not cochee and presence is not None:
                # Jamais toucher une présence signée ou à statut particulier.
                if presence.signature_path or (presence.presence_type or "present") != "present":
                    protegees += 1
                    continue
                db.session.delete(presence)
                retirees += 1
        db.session.commit()
        msg = f"Grille enregistrée : {ajoutees} présence(s) ajoutée(s), {retirees} retirée(s)."
        if protegees:
            msg += f" {protegees} case(s) protégée(s) (signature ou statut) laissée(s) telle(s) quelle(s)."
        flash(msg, "success")
        return redirect(url_for("activite.saisie_grille", atelier_id=atelier.id, mois=mois_str))

    # --- Affichage ---
    seances = _seances_du_mois(atelier.id, annee, mois) if atelier else []
    participants = _participants_de_l_atelier(atelier.id, en_plus) if atelier else []
    presences = {}
    if seances:
        for p in PresenceActivite.query.filter(
            PresenceActivite.session_id.in_([s.id for s in seances])
        ).all():
            presences[(p.participant_id, p.session_id)] = p

    # Recherche d'une personne à ajouter à la grille : couvre TOUS les
    # participants (une liste déroulante tronquée ne suffisait pas), en
    # n'affichant que les correspondances — lisible même avec des milliers
    # de fiches.
    q_personne = (request.args.get("q_personne") or "").strip()
    resultats_ajout = []
    if atelier and q_personne:
        deja = {p.id for p in participants}
        motif = f"%{q_personne}%"
        trouves = (
            Participant.query
            .filter(db.or_(Participant.nom.ilike(motif), Participant.prenom.ilike(motif)))
            .order_by(Participant.nom.asc(), Participant.prenom.asc())
            .limit(30)
            .all()
        )
        resultats_ajout = [p for p in trouves if p.id not in deja]

    return render_template(
        "activite/saisie_grille.html",
        ateliers=ateliers,
        atelier=atelier,
        mois_str=mois_str,
        seances=seances,
        participants=participants,
        presences=presences,
        q_personne=q_personne,
        resultats_ajout=resultats_ajout,
        en_plus=en_plus,
    )


# ---------------------------------------------------------------------------
# Émargements en attente (feuilles papier pas encore saisies)
# ---------------------------------------------------------------------------

def seances_sans_presence_query(secteur: str | None):
    """Séances passées (12 derniers mois) sans aucune présence saisie."""
    today = date.today()
    eff = _session_effective_date_expr()
    q = (
        SessionActivite.query
        .outerjoin(PresenceActivite, PresenceActivite.session_id == SessionActivite.id)
        .filter(
            SessionActivite.is_deleted.is_(False),
            db.func.lower(db.func.coalesce(SessionActivite.statut, "")) != "annulee",
            eff < today,
            eff >= today - timedelta(days=365),
            PresenceActivite.id.is_(None),
        )
    )
    if secteur:
        q = q.filter(SessionActivite.secteur == secteur)
    return q.order_by(eff.asc())


@bp.route("/emargements-attente")
@login_required
def emargements_attente():
    """Le radar des feuilles en retard, groupé par atelier."""
    _require_any_perm("emargement:view", "emargement:edit")
    secteur = _user_secteur()
    if not secteur and not (_is_admin_global() or _has_all_secteurs_scope()):
        flash("Aucun secteur n'est associé à votre compte pour l'activité.", "warning")
        return render_template("activite/emargements_attente.html", groupes=[], total=0)

    seances = seances_sans_presence_query(secteur or None).all()
    today = date.today()

    # Rappels déjà posés (pour ne pas proposer deux fois la relance).
    liens_rappels = {
        r.lien_url
        for r in SuiviRappel.query.filter(
            SuiviRappel.statut == "ouvert",
            SuiviRappel.categorie == "emargement",
        ).all()
    }

    groupes: dict[int, dict] = {}
    total = 0
    for s in seances:
        d = s.rdv_date or s.date_session
        if d is None:
            continue
        total += 1
        g = groupes.setdefault(s.atelier_id, {"atelier": s.atelier, "seances": []})
        g["seances"].append({
            "session": s,
            "date": d,
            "retard_jours": (today - d).days,
            "relance_posee": url_for("activite.emargement", session_id=s.id) in liens_rappels,
        })
    ordre = sorted(groupes.values(), key=lambda g: -max(x["retard_jours"] for x in g["seances"]))
    return render_template("activite/emargements_attente.html", groupes=ordre, total=total)


@bp.post("/emargements-attente/relancer")
@login_required
def emargements_relancer():
    """Pose un rappel « feuille à récupérer » dans le centre de suivi."""
    require_perm("emargement:edit")(lambda: None)()
    try:
        session_id = int(request.form.get("session_id") or 0)
    except Exception:
        session_id = 0
    s = db.session.get(SessionActivite, session_id)
    if s is None or not _session_est_accessible(s):
        flash("Séance introuvable ou hors de ta portée.", "danger")
        return redirect(url_for("activite.emargements_attente"))
    lien = url_for("activite.emargement", session_id=s.id)
    deja = SuiviRappel.query.filter(
        SuiviRappel.statut == "ouvert",
        SuiviRappel.categorie == "emargement",
        SuiviRappel.lien_url == lien,
    ).first()
    if deja is not None:
        flash("Une relance est déjà en cours pour cette séance.", "info")
        return redirect(url_for("activite.emargements_attente"))
    d = s.rdv_date or s.date_session
    atelier_nom = s.atelier.nom if s.atelier else f"Atelier #{s.atelier_id}"
    db.session.add(SuiviRappel(
        titre=f"Feuille d'émargement à récupérer : {atelier_nom} — séance du {d.strftime('%d/%m/%Y') if d else '?'}",
        description="La séance est passée mais aucune présence n'est saisie. Récupérer la feuille auprès de l'animateur, puis la saisir (grille de rattrapage).",
        categorie="emargement",
        priorite="warn",
        secteur=s.secteur,
        echeance=date.today() + timedelta(days=3),
        lien_url=lien,
        created_by_user_id=getattr(current_user, "id", None),
    ))
    db.session.commit()
    flash("Relance posée : elle apparaît dans « À traiter ».", "success")
    return redirect(url_for("activite.emargements_attente"))


# ---------------------------------------------------------------------------
# Feuille du mois imprimable (le papier, miroir exact de la grille)
# ---------------------------------------------------------------------------

@bp.route("/saisie-grille/imprimer")
@login_required
def saisie_grille_imprimer():
    """Feuille d'émargement papier du mois : habitués en lignes, dates en
    colonnes — le miroir exact de la grille de saisie. L'animateur coche
    sur le papier, l'accueil recopie case pour case dans la grille."""
    _require_any_perm("emargement:view", "emargement:edit")

    ateliers = _ateliers_visibles()
    annee, mois = _mois_arg()
    try:
        atelier_id = int(request.args.get("atelier_id") or 0)
    except Exception:
        atelier_id = 0
    atelier = next((a for a in ateliers if a.id == atelier_id), None)
    if atelier is None:
        flash("Choisis d'abord un atelier dans la grille.", "warning")
        return redirect(url_for("activite.saisie_grille", mois=f"{annee:04d}-{mois:02d}"))

    seances = _seances_du_mois(atelier.id, annee, mois)
    participants = _participants_de_l_atelier(atelier.id, [])
    # Colonnes vides si les séances du mois n'existent pas encore : la date
    # s'écrit à la main, la séance se crée au moment de la saisie.
    nb_colonnes_vides = max(0, 5 - len(seances))
    mois_label = f"{MOIS_FR[mois - 1]} {annee}"
    return render_template(
        "activite/saisie_grille_imprimer.html",
        atelier=atelier,
        mois_str=f"{annee:04d}-{mois:02d}",
        mois_label=mois_label,
        seances=seances,
        participants=participants,
        nb_colonnes_vides=nb_colonnes_vides,
        lignes_vides=8,
    )
