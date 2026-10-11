"""Caisse : état, fond de caisse, comptage (arrêté), dépôts en banque.

Écrit pour quelqu'un qui découvre la gestion d'une caisse : chaque écran
explique le vocabulaire, et le guide « Je compte la caisse et je fais le
dépôt » accompagne l'opération de bout en bout.
"""
from datetime import date, datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.main.common import bp
from app.models import CaisseMouvement
from app.rbac import can, require_perm
from app.services.audit import journaliser
from app.services.caisse import ajuster, enregistrer_comptage, etat_caisse, journal, verrouiller_caisse
from app.utils.montants import parse_montant


def _montant_form(champ: str) -> float | None:
    return parse_montant(request.form.get(champ), negatif=True)


def _jeton() -> str | None:
    jeton = (request.form.get("jeton") or "").strip()
    return jeton[:64] if len(jeton) >= 16 else None


def _deja_traite() -> bool:
    """Formulaire déjà enregistré (double clic, renvoi après coupure)."""
    jeton = _jeton()
    return bool(jeton and CaisseMouvement.query.filter_by(jeton=jeton).first())


def _date_form(champ: str) -> date:
    try:
        return datetime.strptime((request.form.get(champ) or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return date.today()


@bp.route("/caisse")
@login_required
@require_perm("caisse:view")
def caisse():
    return render_template(
        "caisse.html",
        etat=etat_caisse(),
        mouvements=journal(),
        peut_editer=can("caisse:edit"),
        today=date.today(),
    )


@bp.post("/caisse/fond")
@login_required
@require_perm("caisse:edit")
def caisse_fond():
    montant = _montant_form("montant")
    if montant is None or montant < 0:
        flash("Montant du fond de caisse invalide.", "danger")
        return redirect(url_for("main.caisse"))
    verrouiller_caisse()
    if _deja_traite():
        flash("Ce fond de caisse a déjà été enregistré.", "info")
        return redirect(url_for("main.caisse"))
    db.session.add(CaisseMouvement(
        jeton=_jeton(),
        type_mouvement="fond", canal="especes", montant=montant,
        date_mouvement=date.today(),
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        created_by_user_id=getattr(current_user, "id", None),
    ))
    db.session.commit()
    journaliser("caisse.fond", cible=f"{montant:.2f} €")
    flash(f"Fond de caisse réglé à {montant:.2f} €.", "success")
    return redirect(url_for("main.caisse"))


@bp.post("/caisse/comptage")
@login_required
@require_perm("caisse:edit")
def caisse_comptage():
    montant = _montant_form("montant_constate")
    if montant is None or montant < 0:
        flash("Montant compté invalide.", "danger")
        return redirect(url_for("main.caisse"))
    if _deja_traite():
        flash("Ce comptage a déjà été enregistré.", "info")
        return redirect(url_for("main.caisse"))
    comptage = enregistrer_comptage(
        montant, jeton=_jeton(),
        commentaire=(request.form.get("commentaire") or "").strip() or None,
        user_id=getattr(current_user, "id", None),
    )
    journaliser("caisse.comptage", cible=f"constaté {montant:.2f} €, écart {comptage.ecart:+.2f} €")
    if abs(comptage.ecart or 0) < 0.01:
        flash(f"Caisse comptée : {montant:.2f} €, tout est juste ✔", "success")
    else:
        flash(
            f"Caisse comptée : {montant:.2f} € — écart de {comptage.ecart:+.2f} € par rapport au théorique. "
            "L'écart est tracé et le théorique repart du montant réel.",
            "warning",
        )
    return redirect(url_for("main.caisse"))


@bp.post("/caisse/depot")
@login_required
@require_perm("caisse:edit")
def caisse_depot():
    """Enregistre un dépôt en banque (espèces et/ou chèques)."""
    verrouiller_caisse()
    if _deja_traite():
        flash("Ce dépôt a déjà été enregistré.", "info")
        return redirect(url_for("main.caisse"))
    etat = etat_caisse()
    especes = _montant_form("montant_especes") or 0.0
    cheques = _montant_form("montant_cheques") or 0.0
    try:
        nb_cheques = int(request.form.get("nb_cheques") or 0)
    except Exception:
        nb_cheques = 0

    if especes <= 0 and cheques <= 0:
        flash("Indiquez au moins un montant (espèces ou chèques) à déposer.", "danger")
        return redirect(url_for("main.caisse"))
    if especes > etat["theorique_especes"]:
        flash(
            f"Impossible de déposer {especes:.2f} € en espèces : la caisse n'en contient que "
            f"{etat['theorique_especes']:.2f} € en théorie (faites d'abord un comptage si le réel diffère).",
            "danger",
        )
        return redirect(url_for("main.caisse"))
    if cheques > etat["cheques_en_attente"] + 0.001:
        flash(
            f"Impossible de déposer {cheques:.2f} € de chèques : seuls "
            f"{etat['cheques_en_attente']:.2f} € de chèques sont en attente de dépôt.",
            "danger",
        )
        return redirect(url_for("main.caisse"))
    if cheques > 0 and nb_cheques <= 0:
        flash("Indiquez le nombre de chèques déposés.", "danger")
        return redirect(url_for("main.caisse"))

    jour = _date_form("date_depot")
    commentaire = (request.form.get("commentaire") or "").strip() or None
    # Horodatage COMMUN aux lignes du même dépôt : c'est lui qui permet au
    # bordereau de regrouper espèces + chèques déposés ensemble.
    from app.utils.dates import utcnow
    ts = utcnow()
    ids = []
    if especes > 0:
        m = CaisseMouvement(type_mouvement="depot", canal="especes", montant=especes, jeton=_jeton(),
                            date_mouvement=jour, commentaire=commentaire, created_at=ts,
                            created_by_user_id=getattr(current_user, "id", None))
        db.session.add(m)
        ids.append(m)
    if cheques > 0:
        m = CaisseMouvement(type_mouvement="depot", canal="cheque", montant=cheques,
                            jeton=(None if especes > 0 else _jeton()),
                            nb_cheques=nb_cheques, date_mouvement=jour, commentaire=commentaire, created_at=ts,
                            created_by_user_id=getattr(current_user, "id", None))
        db.session.add(m)
        ids.append(m)
    db.session.commit()
    journaliser("caisse.depot", cible=f"espèces {especes:.2f} € + chèques {cheques:.2f} € ({nb_cheques})")
    flash("Dépôt enregistré. Vous pouvez imprimer le bordereau pour l'apporter à la banque.", "success")
    return redirect(url_for("main.caisse_bordereau", mouvement_id=ids[0].id))


@bp.route("/caisse/depot/<int:mouvement_id>/bordereau")
@login_required
@require_perm("caisse:view")
def caisse_bordereau(mouvement_id: int):
    """Bordereau imprimable d'un dépôt (regroupe espèces + chèques du même jour)."""
    m = db.get_or_404(CaisseMouvement, mouvement_id)
    if m.type_mouvement != "depot":
        flash("Ce mouvement n'est pas un dépôt.", "danger")
        return redirect(url_for("main.caisse"))
    lignes = (
        CaisseMouvement.query
        .filter(
            CaisseMouvement.type_mouvement == "depot",
            CaisseMouvement.date_mouvement == m.date_mouvement,
            CaisseMouvement.created_at == m.created_at,
        )
        .all()
    )
    if m not in lignes:
        lignes = [m]
    total = round(sum(x.montant for x in lignes), 2)
    return render_template("caisse_bordereau.html", lignes=lignes, depot=m, total=total)


@bp.post("/caisse/ajustement")
@login_required
@require_perm("caisse:edit")
def caisse_ajustement():
    """Ajustement manuel du théorique d'espèces, motif obligatoire (audit 2.2)."""
    montant = _montant_form("montant")
    motif = (request.form.get("motif") or "").strip()
    if montant is None:
        flash("Montant invalide.", "danger")
        return redirect(url_for("main.caisse"))
    if _deja_traite():
        flash("Cet ajustement a déjà été enregistré.", "info")
        return redirect(url_for("main.caisse"))
    try:
        mouvement = ajuster(montant, motif, user_id=getattr(current_user, "id", None))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.caisse"))
    if _jeton():
        mouvement.jeton = _jeton()
        db.session.commit()
    journaliser("caisse.ajustement", cible=f"{montant:+.2f} €", details={"motif": motif})
    flash(f"Ajustement de {montant:+.2f} € enregistré.", "success")
    return redirect(url_for("main.caisse"))


@bp.route("/caisse/a-qualifier", methods=["GET", "POST"])
@login_required
@require_perm("caisse:view")
def caisse_a_qualifier():
    """Sommes reprises d'une ancienne version sans mode de règlement sûr.

    Pour chacune, une personne habilitée indique le ou les modes réels et si
    l'argent doit entrer dans le théorique de caisse (non encore compté) ou
    s'il a déjà été absorbé par un comptage. Rien n'est tranché d'office.
    """
    from app.models import Encaissement, MODES_PAIEMENT, MODES_PAIEMENT_LABELS
    from app.services.encaissements import EncaissementErreur, a_qualifier, qualifier
    if request.method == "POST":
        if not can("caisse:edit"):
            abort(403)
        encaissement = db.session.get(Encaissement, request.form.get("encaissement_id", type=int) or 0)
        if encaissement is None:
            abort(404)
        parts = []
        for i in range(3):
            mode = (request.form.get(f"mode_{i}") or "").strip()
            montant = (request.form.get(f"montant_{i}") or "").strip()
            if mode and montant:
                parts.append((mode, montant))
        choix = request.form.get("caisse")
        if choix not in {"dans", "hors"}:
            flash("Indiquez si cette somme doit entrer dans le théorique de caisse ou a déjà été comptée.", "danger")
            return redirect(url_for("main.caisse_a_qualifier"))
        try:
            resultat = qualifier(encaissement, parts, dans_caisse=(choix == "dans"),
                                 user_id=getattr(current_user, "id", None))
            db.session.commit()
        except EncaissementErreur as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("main.caisse_a_qualifier"))
        journaliser("encaissement.qualification", cible=f"encaissement #{encaissement.id}",
                    details={"parts": [[e.mode, e.montant] for e in resultat], "dans_caisse": choix == "dans"})
        flash("Somme qualifiée.", "success")
        return redirect(url_for("main.caisse_a_qualifier"))
    return render_template(
        "caisse_a_qualifier.html",
        encaissements=a_qualifier(),
        modes=MODES_PAIEMENT, modes_labels=MODES_PAIEMENT_LABELS,
        peut_editer=can("caisse:edit"),
    )


@bp.route("/caisse/rapprochement-bulletins", methods=["GET", "POST"])
@login_required
@require_perm("caisse:view")
def caisse_rapprochement_bulletins():
    """Règlements notés sur d'anciens bulletins : ni prouvés reportés, ni
    exclus. Une personne habilitée confirme le report ou constate un
    encaissement distinct ; rien n'est ajouté d'office (défaut A)."""
    from app.services import rapprochement_reglements as rr
    from app.services.encaissements import EncaissementErreur
    from app.services.sauvegarde import lister_lots
    if request.method == "POST":
        if not can("caisse:edit"):
            abort(403)
        action = request.form.get("action")
        ligne_id = request.form.get("ligne_id", type=int) or 0
        user_id = getattr(current_user, "id", None)
        try:
            if action == "confirmer":
                ligne = rr.confirmer_report(ligne_id, user_id=user_id, note=request.form.get("note"))
                message = "Report confirmé : rien n'a été ajouté."
            elif action == "constater":
                encaissement = rr.constater_encaissement(ligne_id, request.form.get("montant"), user_id=user_id,
                                                         note=request.form.get("note"))
                ligne = encaissement
                message = (f"{encaissement.montant:.2f} € ajoutés aux sommes « à qualifier » : "
                           "précisez-y le ou les modes et la prise en compte en caisse.")
            elif action == "corriger":
                rr.corriger_doublon(ligne_id, user_id=user_id, note=request.form.get("note"))
                message = "Somme historique en doublon annulée (contre-passation motivée)."
            elif action == "verifier":
                rr.marquer_verifie(ligne_id, user_id=user_id, note=request.form.get("note"))
                message = "Vérification enregistrée : rien n'a été modifié en caisse."
            elif action == "comparer":
                base = (request.form.get("base") or "").strip()
                compte = rr.comparer_avec_sauvegarde(base)
                journaliser("rapprochement.comparaison_sauvegarde", cible=base[:200], details=compte)
                flash(f"Sauvegarde « {base} » lue : {compte['lues']} bulletin(s) avec règlement ; "
                      f"{compte['a_rapprocher']} nouveau(x) à rapprocher, {compte['reporte']} reporté(s), "
                      f"{compte['suivi']} déjà suivi(s).", "success")
                return redirect(url_for("main.caisse_rapprochement_bulletins"))
            else:
                abort(400)
            db.session.commit()
        except EncaissementErreur as exc:
            db.session.rollback()
            flash(str(exc), "info" if "déjà" in str(exc) else "danger")
            return redirect(url_for("main.caisse_rapprochement_bulletins"))
        journaliser("rapprochement.decision", cible=f"rapprochement #{ligne_id}",
                    details={"action": action, "montant": request.form.get("montant"),
                             "note": (request.form.get("note") or "")[:255]})
        flash(message, "success")
        return redirect(url_for("main.caisse_rapprochement_bulletins"))
    return render_template(
        "caisse_rapprochement_bulletins.html",
        a_rapprocher=rr.a_rapprocher(), reportes=rr.reportes_deduits(), decides=rr.decides(),
        a_corriger=rr.a_corriger(), exige_verification=rr.exige_verification,
        manquant_maximal=rr.manquant_maximal,
        resume=rr.resume(), lots=lister_lots(), peut_editer=can("caisse:edit"),
    )


@bp.route("/controle/anomalies-montants", methods=["GET", "POST"])
@login_required
@require_perm("caisse:view")
def anomalies_montants():
    """Montants non finis hérités (NaN, infini) : liste et correction motivée."""
    from app.services import anomalies_montants as anomalies
    if request.method == "POST":
        if not can("caisse:edit"):
            abort(403)
        try:
            anomalies.corriger(
                (request.form.get("table") or "").strip(), (request.form.get("colonne") or "").strip(),
                request.form.get("ligne_id", type=int) or 0, request.form.get("montant"),
                request.form.get("motif"), user_id=getattr(current_user, "id", None),
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        else:
            flash("Montant corrigé. L'ancienne valeur est conservée au journal d'audit.", "success")
        return redirect(url_for("main.anomalies_montants"))
    return render_template("anomalies_montants.html", anomalies=anomalies.lister(), peut_editer=can("caisse:edit"))
