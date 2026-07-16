import os
import base64
import json
from datetime import datetime

from app.utils.dates import utcnow

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    abort,
    current_app,
    jsonify,
    flash,
)

from app.extensions import db, csrf
from app.models import (
    SessionActivite,
    AtelierActivite,
    Participant,
    PresenceActivite,
    Quartier,
    AtelierCapaciteMois,
    Questionnaire,
    Question,
    QuestionnaireResponseGroup,
    QuestionResponse,
)

from . import bp
from app.activite.services.docx_utils import generate_individuel_mensuel_docx
from app.services.quartiers import normalize_quartier_for_ville


def _ensure_month_capacity(atelier: AtelierActivite, session: SessionActivite):
    if atelier.type_atelier != "INDIVIDUEL_MENSUEL":
        return
    if not session.rdv_date:
        return
    annee, mois = session.rdv_date.year, session.rdv_date.month
    cap = AtelierCapaciteMois.query.filter_by(atelier_id=atelier.id, annee=annee, mois=mois).first()
    if cap:
        return
    heures = float(atelier.heures_dispo_defaut_mois or 0.0)
    db.session.add(AtelierCapaciteMois(atelier_id=atelier.id, annee=annee, mois=mois, heures_dispo=heures, locked=False))
    db.session.commit()


# Détection d'homonymes : logique partagée avec la création manuelle
# (/participants/new). Les alias conservent les noms historiques du module.
from app.services.doublons import (  # noqa: E402
    candidats_doublons as _candidats_doublons,
    normaliser_nom as _normaliser_nom,
    squelette_nom as _squelette_nom,
)


def _get_open_session_by_pin(pin: str):
    if not pin:
        return None
    pin = pin.strip()
    if not pin:
        return None
    return (
        SessionActivite.query
        .filter_by(kiosk_open=True, kiosk_pin=pin)
        .filter(SessionActivite.is_deleted.is_(False))
        .order_by(SessionActivite.created_at.desc())
        .first()
    )


def _get_open_session_by_token(token: str):
    if not token:
        return None
    token = token.strip()
    if not token:
        return None
    return (
        SessionActivite.query
        .filter_by(kiosk_open=True, kiosk_token=token)
        .filter(SessionActivite.is_deleted.is_(False))
        .first()
    )


def _session_label(s: SessionActivite):
    atelier = db.session.get(AtelierActivite, s.atelier_id)
    secteur = s.secteur
    nom = atelier.nom if atelier else "Atelier"
    if s.session_type == "COLLECTIF":
        d = s.date_session.isoformat() if s.date_session else ""
        h = ""
        if s.heure_debut:
            h = s.heure_debut
            if s.heure_fin:
                h += f"-{s.heure_fin}"
        return f"{secteur} — {nom} — {d} {h}".strip()
    else:
        d = s.rdv_date.isoformat() if s.rdv_date else ""
        h = ""
        if s.rdv_debut:
            h = s.rdv_debut
            if s.rdv_fin:
                h += f"-{s.rdv_fin}"
        return f"{secteur} — {nom} — RDV {d} {h}".strip()


def _questionnaires_for_session(session: SessionActivite) -> list[Questionnaire]:
    q = Questionnaire.query.filter_by(is_active=True).order_by(Questionnaire.nom.asc()).all()
    result = []
    for questionnaire in q:
        secteurs = {s.secteur for s in questionnaire.secteurs}
        ateliers = {a.atelier_id for a in questionnaire.ateliers}
        if secteurs and session.secteur not in secteurs:
            continue
        if ateliers and session.atelier_id not in ateliers:
            continue
        result.append(questionnaire)
    return result


def _open_sessions_today() -> list[dict]:
    """Liste des ateliers actuellement ouverts au kiosque.

    La source de vérité est ``kiosk_open`` : une session peut être ouverte
    pour émargement même si sa date métier n'est pas exactement la date du
    serveur (préparation la veille, PC serveur en UTC, rattrapage, etc.).
    Restreindre strictement à ``date.today()`` masquait donc des ateliers
    pourtant ouverts et accessibles par PIN/token.
    """
    sessions = (
        SessionActivite.query.filter_by(kiosk_open=True)
        .filter(SessionActivite.is_deleted.is_(False))
        .order_by(
            SessionActivite.date_session.asc(),
            SessionActivite.rdv_date.asc(),
            SessionActivite.heure_debut.asc(),
            SessionActivite.rdv_debut.asc(),
            SessionActivite.created_at.desc(),
        )
        .limit(300)
        .all()
    )

    entries = []
    for s in sessions:
        atelier = db.session.get(AtelierActivite, s.atelier_id)
        entries.append({
            "token": s.kiosk_token,
            "pin": s.kiosk_pin,
            "label": _session_label(s),
            "secteur": s.secteur,
            "atelier": atelier.nom if atelier else "Atelier",
            "type": s.session_type,
            "date": (s.date_session or s.rdv_date),
            "debut": s.heure_debut or s.rdv_debut,
            "fin": s.heure_fin or s.rdv_fin,
        })
    return entries


@bp.route("/", methods=["GET", "POST"])
@csrf.exempt
def kiosk_home():
    """Page publique: saisie PIN + liste des sessions ouvertes."""
    if request.method == "POST":
        pin = (request.form.get("pin") or "").strip()
        s = _get_open_session_by_pin(pin)
        if not s:
            flash("Code invalide ou session fermée.", "danger")
            return redirect(url_for("kiosk.kiosk_home"))
        return redirect(url_for("kiosk.kiosk_session", token=s.kiosk_token))

    return render_template("kiosk/index.html", sessions=_open_sessions_today())


@bp.route("/programme")
def kiosk_programme():
    """Programme en direct (lecture seule) : les ateliers ouverts du jour,
    consultables sans émarger. Pratique aussi en affichage mural dans le hall."""
    return render_template("kiosk/programme.html", sessions=_open_sessions_today())


@bp.route("/session/<token>/search")
def kiosk_search(token: str):
    """Recherche participants (protégée par token de session kiosque)."""
    s = _get_open_session_by_token(token)
    if not s:
        return jsonify({"results": []})
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    q_norm = q.replace("%", "").replace("_", "")

    # Recherche simple nom/prénom (LIKE). SQLite: case-insensitive sur ASCII, mais c'est ok.
    candidates = (
        Participant.query.filter(
            (Participant.nom.ilike(f"%{q_norm}%")) | (Participant.prenom.ilike(f"%{q_norm}%"))
        )
        .order_by(Participant.nom.asc(), Participant.prenom.asc())
        .limit(12)
        .all()
    )

    res = []
    for p in candidates:
        label = f"{p.nom} {p.prenom}"
        if p.ville:
            label += f" · {p.ville}"
        res.append({"id": p.id, "label": label})
    return jsonify({"results": res})


@bp.route("/session/<token>", methods=["GET", "POST"])
@csrf.exempt
def kiosk_session(token: str):
    """Page publique d'émargement d'une session précise."""
    s = _get_open_session_by_token(token)
    if not s:
        abort(404)

    atelier = db.get_or_404(AtelierActivite, s.atelier_id)
    motifs = atelier.motifs() or []
    quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()

    message_ok = None
    recu = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_participant":
            nom = (request.form.get("nom") or "").strip()
            prenom = (request.form.get("prenom") or "").strip()
            ville = (request.form.get("ville") or "").strip() or None
            email = (request.form.get("email") or "").strip() or None
            telephone = (request.form.get("telephone") or "").strip() or None
            type_public = (request.form.get("type_public") or "").strip() or "H"
            genre = (request.form.get("genre") or "").strip() or None
            date_naissance = request.form.get("date_naissance") or None
            quartier_id = request.form.get("quartier_id") or None

            if not nom or not prenom:
                flash("Nom et prénom obligatoires.", "danger")
                return redirect(url_for("kiosk.kiosk_session", token=token))

            # Anti-doublons : avant de créer, proposer les personnes proches
            # (sauf si la personne a confirmé « créer quand même »).
            if request.form.get("force_creation") != "1":
                candidats = _candidats_doublons(nom, prenom)
                if candidats:
                    return render_template(
                        "kiosk/session.html",
                        session=s,
                        atelier=atelier,
                        session_label=_session_label(s),
                        motifs=motifs,
                        quartiers=quartiers,
                        message_ok=None,
                        recu=None,
                        highlight=None,
                        highlight_label=None,
                        token=token,
                        feedback_url=url_for("kiosk.kiosk_feedback", token=token, _external=True),
                        doublons_candidats=candidats,
                        pending=request.form,
                    )

            qid = normalize_quartier_for_ville(ville, quartier_id)

            dn = None
            if date_naissance:
                try:
                    dn = datetime.strptime(date_naissance, "%Y-%m-%d").date()
                except Exception:
                    dn = None

            p = Participant(
                nom=nom,
                prenom=prenom,
                ville=ville,
                email=email,
                telephone=telephone,
                type_public=type_public,
                genre=genre,
                date_naissance=dn,
                quartier_id=qid,
            )
            db.session.add(p)
            db.session.commit()
            flash("Participant créé. Sélectionne-le ci-dessous puis signe.", "success")
            return redirect(url_for("kiosk.kiosk_session", token=token, highlight=p.id))

        if action == "emarger":
            participant_id = request.form.get("participant_id")
            motif = request.form.get("motif") or None
            motif_autre = (request.form.get("motif_autre") or "").strip() or None
            signature_data = request.form.get("signature_data")

            if not participant_id or str(participant_id).lower() in {"null", "undefined"}:
                flash("Choisis ton nom dans la liste.", "danger")
                return redirect(url_for("kiosk.kiosk_session", token=token))

            participant = db.session.get(Participant, int(participant_id))
            if not participant:
                flash("Participant introuvable.", "danger")
                return redirect(url_for("kiosk.kiosk_session", token=token))

            sig_path = None
            if signature_data and signature_data.startswith("data:image"):
                try:
                    header, b64data = signature_data.split(",", 1)
                    binary = base64.b64decode(b64data)
                    sig_dir = os.path.join(current_app.instance_path, "signatures_tmp")
                    os.makedirs(sig_dir, exist_ok=True)
                    sig_filename = f"sig_kiosk_s{s.id}_p{participant.id}_{int(utcnow().timestamp())}.png"
                    sig_path = os.path.join(sig_dir, sig_filename)
                    with open(sig_path, "wb") as f:
                        f.write(binary)
                except Exception:
                    sig_path = None

            try:
                pr = PresenceActivite(
                    session_id=s.id,
                    participant_id=participant.id,
                    motif=motif,
                    motif_autre=motif_autre,
                    signature_path=sig_path,
                )
                db.session.add(pr)
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("Tu es déjà émargé(e) sur cette séance.", "warning")
                return redirect(url_for("kiosk.kiosk_session", token=token))

            # Actions post (individuel mensuel)
            if s.session_type == "INDIVIDUEL_MENSUEL":
                _ensure_month_capacity(atelier, s)
                generate_individuel_mensuel_docx(app=current_app, atelier=atelier, annee=s.rdv_date.year, mois=s.rdv_date.month)

            message_ok = "Merci, c’est bon !"
            recu = pr.id

    highlight = request.args.get("highlight")
    highlight_label = None
    if highlight:
        try:
            hp = db.session.get(Participant, int(highlight))
            if hp:
                highlight_label = f"{hp.nom} {hp.prenom}" + (f" · {hp.ville}" if hp.ville else "")
        except Exception:
            highlight = None

    label = _session_label(s)

    return render_template(
        "kiosk/session.html",
        session=s,
        atelier=atelier,
        session_label=label,
        motifs=motifs,
        quartiers=quartiers,
        message_ok=message_ok,
        recu=recu,
        highlight=highlight,
        highlight_label=highlight_label,
        token=token,
        feedback_url=url_for("kiosk.kiosk_feedback", token=token, _external=True),
    )


@bp.route("/session/<token>/feedback", methods=["GET", "POST"])
@csrf.exempt
def kiosk_feedback(token: str):
    """Questionnaire public post-séance (ressenti participant)."""
    s = _get_open_session_by_token(token)
    if not s:
        abort(404)

    atelier = db.session.get(AtelierActivite, s.atelier_id)
    questionnaires = _questionnaires_for_session(s)

    presences = (
        db.session.query(Participant)
        .join(PresenceActivite, PresenceActivite.participant_id == Participant.id)
        .filter(PresenceActivite.session_id == s.id)
        .order_by(Participant.nom.asc(), Participant.prenom.asc())
        .all()
    )

    selected_questionnaire = None
    selected_qid = request.values.get("questionnaire_id", type=int)
    if selected_qid:
        selected_questionnaire = next((q for q in questionnaires if q.id == selected_qid), None)
    if not selected_questionnaire and questionnaires:
        selected_questionnaire = questionnaires[0]

    questions = []
    options_map = {}
    if selected_questionnaire:
        questions = (
            Question.query.filter_by(questionnaire_id=selected_questionnaire.id)
            .order_by(Question.position.asc(), Question.id.asc())
            .all()
        )
        for question in questions:
            if question.options_json:
                try:
                    options_map[question.id] = json.loads(question.options_json)
                except Exception:
                    options_map[question.id] = []
            else:
                options_map[question.id] = []

    if request.method == "POST":
        if not selected_questionnaire:
            flash("Aucun questionnaire disponible pour cette séance.", "danger")
            return redirect(url_for("kiosk.kiosk_feedback", token=token))

        participant_id = request.form.get("participant_id", type=int)

        group = QuestionnaireResponseGroup(
            questionnaire_id=selected_questionnaire.id,
            participant_id=participant_id,
            session_id=s.id,
            atelier_id=s.atelier_id,
            secteur=s.secteur,
            created_by_user_id=None,
        )
        db.session.add(group)
        db.session.flush()

        for question in questions:
            key = f"question_{question.id}"
            value = request.form.getlist(key) if question.kind == "multi" else request.form.get(key)
            response = QuestionResponse(response_group_id=group.id, question_id=question.id)
            if question.kind == "scale":
                try:
                    response.value_number = float(value) if value not in (None, "") else None
                except Exception:
                    response.value_number = None
            elif question.kind == "yesno":
                response.value_text = value or None
            elif question.kind == "multi":
                response.value_json = json.dumps(value or [], ensure_ascii=False)
            else:
                response.value_text = (value or "").strip() or None
            db.session.add(response)

        db.session.commit()
        flash("Merci ! Ton ressenti a bien été enregistré.", "success")
        return redirect(url_for("kiosk.kiosk_feedback", token=token, questionnaire_id=selected_questionnaire.id))

    return render_template(
        "kiosk/feedback.html",
        token=token,
        session=s,
        atelier=atelier,
        session_label=_session_label(s),
        questionnaires=questionnaires,
        selected_questionnaire=selected_questionnaire,
        questions=questions,
        options_map=options_map,
        presences=presences,
    )


# ---------------------------------------------------------------------------
# Signature à distance : lien personnel à usage unique (/kiosk/signer/<jeton>)
# ---------------------------------------------------------------------------

@bp.route("/signer/<token>", methods=["GET", "POST"])
@csrf.exempt
def signer(token: str):
    """La personne ouvre SON lien (reçu par SMS/WhatsApp), voit sa présence
    (atelier, date) et signe sur son téléphone. Usage unique : le jeton est
    effacé dès que la signature est posée."""
    token = (token or "").strip()
    pr = (
        PresenceActivite.query.filter_by(signature_token=token).first()
        if token else None
    )
    if pr is None or pr.signature_path:
        return render_template("kiosk/signer.html", presence=None), 404

    s = pr.session
    atelier = s.atelier if s else None
    participant = pr.participant

    if request.method == "POST":
        signature_data = request.form.get("signature_data")
        sig_path = None
        if signature_data and signature_data.startswith("data:image"):
            try:
                header, b64data = signature_data.split(",", 1)
                binary = base64.b64decode(b64data)
                sig_dir = os.path.join(current_app.instance_path, "signatures_tmp")
                os.makedirs(sig_dir, exist_ok=True)
                sig_filename = f"sig_distance_s{s.id}_p{participant.id}_{int(utcnow().timestamp())}.png"
                sig_path = os.path.join(sig_dir, sig_filename)
                with open(sig_path, "wb") as f:
                    f.write(binary)
            except Exception:
                sig_path = None
        if not sig_path:
            flash("La signature est vide : signe dans le cadre puis valide.", "danger")
            return redirect(url_for("kiosk.signer", token=token))

        pr.signature_path = sig_path
        pr.signature_token = None  # usage unique : le lien meurt ici
        db.session.commit()
        return render_template("kiosk/signer.html", presence=pr, session=s,
                               atelier=atelier, participant=participant, signe=True)

    return render_template("kiosk/signer.html", presence=pr, session=s,
                           atelier=atelier, participant=participant, signe=False)
