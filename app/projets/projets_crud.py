import os
import json
from io import BytesIO
from datetime import date

from app.utils.dates import utcnow
from flask import render_template, request, redirect, url_for, flash, abort, current_app, send_file
from flask_login import login_required, current_user
from app.rbac import require_perm, can, can_access_secteur
from werkzeug.utils import secure_filename

from app.extensions import db
from app.services.audit import journaliser
from app.services.storage import ensure_upload_subdir, media_relpath, send_media_file
from app.services.indicators import (
    INDICATOR_PACKS,
    INDICATOR_METRIC_GROUPS,
    INDICATOR_TEMPLATES,
    PERIOD_CHOICES,
    TARGET_OP_CHOICES,
)
from app.utils.delete_guard import commit_delete

from app.models import (
    Projet,
    Subvention,
    SubventionProjet,
    BudgetPrevisionnel,
    BudgetPrevisionnelLigne,
    AppelProjetBudget,
    AtelierActivite,
    ProjetAtelier,
    ProjetIndicateur,
    ProjetJournalEntry,
    ProjetAction,
    ProjetActionAtelier,
    ProjetJalon,
    Competence,
    Objectif,
    objectif_competence,
    projet_competence,
    Referentiel,
)


from app.projets.helpers_projet import (
    _collect_project_depenses,
    _money,
    _pct,
    _projet_finance_years,
)
from app.projets.common import (
    ALLOWED_CR,
    bp,
    can_see_secteur,
)

def _projet_finance_context(projet: Projet, year: int) -> dict:
    # Budget prévisionnel global filtré sur ce projet
    prev_lines = (
        BudgetPrevisionnelLigne.query
        .join(BudgetPrevisionnel, BudgetPrevisionnelLigne.budget_id == BudgetPrevisionnel.id)
        .filter(
            BudgetPrevisionnelLigne.projet_id == projet.id,
            BudgetPrevisionnel.secteur == projet.secteur,
            BudgetPrevisionnel.annee == year,
        )
        .order_by(BudgetPrevisionnelLigne.nature.asc(), BudgetPrevisionnelLigne.compte.asc(), BudgetPrevisionnelLigne.ordre.asc(), BudgetPrevisionnelLigne.id.asc())
        .all()
    )

    prev_charges = _money(sum(float(l.montant or 0) for l in prev_lines if l.nature == "charge"))
    prev_produits = _money(sum(float(l.montant or 0) for l in prev_lines if l.nature == "produit"))

    # Ancien budget AAP projet, conservé pour compatibilité
    aap_charges = list(getattr(projet, "charges_projet", []) or [])
    aap_produits = list(getattr(projet, "produits_projet", []) or [])
    aap_charges_prevues = _money(sum(float(c.montant_previsionnel or 0) for c in aap_charges))
    aap_charges_reelles = _money(sum(float(c.montant_reel or 0) for c in aap_charges))
    aap_produits_demandes = _money(sum(float(p.montant_demande or 0) for p in aap_produits))
    aap_produits_accordes = _money(sum(float(p.montant_accorde or 0) for p in aap_produits))
    aap_produits_recus = _money(sum(float(p.montant_recu or 0) for p in aap_produits))
    aap_ventile = _money(sum(float(c.ventile or 0) for c in aap_charges))

    # Base principale : prévisionnel si renseigné, sinon ancien budget projet
    charges_prevues = prev_charges if prev_charges > 0 else aap_charges_prevues
    produits_prevus = prev_produits if prev_produits > 0 else aap_produits_demandes

    subventions = [
        link.subvention for link in getattr(projet, "subventions", []) or []
        if getattr(link, "subvention", None) and int(link.subvention.annee_exercice or 0) == int(year)
    ]
    subventions = sorted(subventions, key=lambda s: (s.nom or "").lower())

    sub_demande = _money(sum(float(s.montant_demande or 0) for s in subventions))
    sub_attribue = _money(sum(float(s.montant_attribue or 0) for s in subventions))
    sub_recu = _money(sum(float(s.montant_recu or 0) for s in subventions))

    dossiers = (
        AppelProjetBudget.query
        .join(BudgetPrevisionnel, AppelProjetBudget.budget_id == BudgetPrevisionnel.id)
        .filter(
            AppelProjetBudget.projet_id == projet.id,
            BudgetPrevisionnel.annee == year,
        )
        .order_by(AppelProjetBudget.created_at.desc(), AppelProjetBudget.id.desc())
        .all()
    )

    depenses = _collect_project_depenses(projet, subventions)
    depenses_total = _money(sum(float(d.montant or 0) for d in depenses))

    linked_subvention_ids = {s.id for s in subventions}
    available_subventions = (
        Subvention.query
        .filter(
            Subvention.secteur == projet.secteur,
            Subvention.annee_exercice == year,
            Subvention.est_archive.is_(False),
        )
        .order_by(Subvention.nom.asc())
        .all()
    )
    available_subventions = [s for s in available_subventions if s.id not in linked_subvention_ids]

    subvention_charge_lines = []
    for sub in subventions:
        for ligne in getattr(sub, "lignes", []) or []:
            if getattr(ligne, "nature", "charge") == "charge":
                subvention_charge_lines.append(ligne)

    # Dépenses saisies via subvention mais non rattachées à une charge projet : utile à repérer
    depenses_non_affectees = [
        d for d in depenses
        if not getattr(d, "charge_projet_id", None)
    ]

    financement_reference = sub_attribue if sub_attribue > 0 else (sub_demande if sub_demande > 0 else produits_prevus)
    encaisse_reference = sub_recu if sub_recu > 0 else aap_produits_recus

    reste_a_financer = _money(charges_prevues - financement_reference)
    reste_a_encaisser = _money(sub_attribue - sub_recu)
    reste_a_depenser = _money(charges_prevues - depenses_total)

    alerts = []
    if charges_prevues <= 0:
        alerts.append({"level": "warn", "text": "Aucun budget de charges prévu n'est détecté pour ce projet sur l'exercice sélectionné."})
    if produits_prevus > 0 and produits_prevus < charges_prevues:
        alerts.append({"level": "warn", "text": f"Produits prévus inférieurs aux charges prévues : reste théorique {_money(charges_prevues - produits_prevus):.2f} €."})
    if sub_attribue > 0 and sub_recu < sub_attribue:
        alerts.append({"level": "warn", "text": f"Subventions accordées non totalement reçues : reste à encaisser {reste_a_encaisser:.2f} €."})
    if depenses_total > charges_prevues and charges_prevues > 0:
        alerts.append({"level": "danger", "text": f"Dépenses engagées supérieures au budget prévu de {_money(depenses_total - charges_prevues):.2f} €."})
    if depenses_non_affectees:
        alerts.append({"level": "info", "text": f"{len(depenses_non_affectees)} dépense(s) sont rattachées à une subvention mais pas encore à une charge projet."})
    if not subventions and not dossiers:
        alerts.append({"level": "info", "text": "Aucun financement ou dossier financeur rattaché à ce projet pour l'exercice sélectionné."})

    if not alerts:
        alerts.append({"level": "ok", "text": "Aucune alerte bloquante détectée sur ce dossier financier."})

    kpis = {
        "charges_prevues": charges_prevues,
        "produits_prevus": produits_prevus,
        "financement_reference": _money(financement_reference),
        "sub_demande": sub_demande,
        "sub_attribue": sub_attribue,
        "sub_recu": sub_recu,
        "depenses_total": depenses_total,
        "reste_a_financer": reste_a_financer,
        "reste_a_encaisser": reste_a_encaisser,
        "reste_a_depenser": reste_a_depenser,
        "solde_previsionnel": _money(produits_prevus - charges_prevues),
        "solde_execution": _money(encaisse_reference - depenses_total),
        "pct_financement": _pct(financement_reference, charges_prevues),
        "pct_recu": _pct(sub_recu, sub_attribue if sub_attribue > 0 else financement_reference),
        "pct_depenses": _pct(depenses_total, charges_prevues),
        "nb_subventions": len(subventions),
        "nb_dossiers": len(dossiers),
        "nb_depenses": len(depenses),
        "nb_depenses_non_affectees": len(depenses_non_affectees),
        "aap_charges_prevues": aap_charges_prevues,
        "aap_charges_reelles": aap_charges_reelles,
        "aap_produits_demandes": aap_produits_demandes,
        "aap_produits_accordes": aap_produits_accordes,
        "aap_produits_recus": aap_produits_recus,
        "aap_ventile": aap_ventile,
    }

    return {
        "year": year,
        "years": _projet_finance_years(projet),
        "kpis": kpis,
        "alerts": alerts,
        "prev_lines": prev_lines,
        "aap_charges": aap_charges,
        "aap_produits": aap_produits,
        "subventions": subventions,
        "available_subventions": available_subventions,
        "subvention_charge_lines": subvention_charge_lines,
        "dossiers": dossiers,
        "depenses": depenses,
        "depenses_non_affectees": depenses_non_affectees,
    }


def ensure_projets_folder():
    return ensure_upload_subdir("projets")

def allowed_cr(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_CR

@bp.route("/projets")
@login_required
@require_perm("projets:view")
def projets_list():
    voir_archives = request.args.get("archives") == "1"
    q = Projet.query.filter(Projet.is_archive.is_(True) if voir_archives else Projet.is_archive.is_(False))
    if not can("scope:all_secteurs"):
        q = q.filter(Projet.secteur == current_user.secteur_assigne)

    projets = q.order_by(Projet.created_at.desc()).all()

    # Compteur d'archives pour proposer le basculement.
    arch_q = Projet.query.filter(Projet.is_archive.is_(True))
    if not can("scope:all_secteurs"):
        arch_q = arch_q.filter(Projet.secteur == current_user.secteur_assigne)
    nb_archives = arch_q.count()

    secteurs = current_app.config.get("SECTEURS", [])
    return render_template("projets_list.html", projets=projets, secteurs=secteurs,
                           voir_archives=voir_archives, nb_archives=nb_archives)


@bp.route("/projets/export.xlsx")
@login_required
@require_perm("projets:view")
def projets_export_xlsx():
    from openpyxl import Workbook

    voir_archives = request.args.get("archives") == "1"
    q = Projet.query.filter(Projet.is_archive.is_(True) if voir_archives else Projet.is_archive.is_(False))
    if not can("scope:all_secteurs"):
        q = q.filter(Projet.secteur == current_user.secteur_assigne)
    projets = q.order_by(Projet.secteur.asc(), Projet.nom.asc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Projets"
    ws.append(["Secteur", "Projet", "Demandé (€)", "Accordé (€)", "Reçu (€)",
               "Engagé (€)", "Reste (€)", "Compte-rendu", "Archivé"])
    for p in projets:
        ws.append([
            p.secteur, p.nom,
            round(float(p.total_demande or 0), 2),
            round(float(p.total_attribue or 0), 2),
            round(float(p.total_recu or 0), 2),
            round(float(p.total_engage or 0), 2),
            round(float(p.total_reste or 0), 2),
            "Oui" if p.cr_filename else "Non",
            "Oui" if p.is_archive else "Non",
        ])
    for col, width in zip("ABCDEFGHI", (16, 38, 13, 13, 13, 13, 13, 13, 9)):
        ws.column_dimensions[col].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name="projets.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/projets/<int:projet_id>/archiver", methods=["POST"])
@login_required
@require_perm("projets:edit")
def projets_archive(projet_id: int):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(getattr(projet, "secteur", None)):
        abort(403)
    projet.is_archive = True
    projet.archived_at = utcnow()
    db.session.commit()
    journaliser("projet.archive", cible=f"projet#{projet.id}", details={"nom": projet.nom})
    flash(f"Projet « {projet.nom} » archivé. Il reste consultable dans les archives.", "warning")
    return redirect(url_for("projets.projets_list"))


@bp.route("/projets/<int:projet_id>/restaurer", methods=["POST"])
@login_required
@require_perm("projets:edit")
def projets_restore(projet_id: int):
    projet = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(getattr(projet, "secteur", None)):
        abort(403)
    projet.is_archive = False
    projet.archived_at = None
    db.session.commit()
    journaliser("projet.restaure", cible=f"projet#{projet.id}", details={"nom": projet.nom})
    flash(f"Projet « {projet.nom} » restauré.", "success")
    return redirect(url_for("projets.projets_list", archives=1))


@bp.route("/projets/<int:projet_id>/dupliquer", methods=["POST"])
@login_required
@require_perm("projets:edit")
def projets_duplicate(projet_id: int):
    """Reconduit un projet : copie structure, actions, indicateurs et liens ateliers.

    Les réponses, dépenses et subventions ne sont PAS copiées (nouvel exercice).
    """
    src = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(getattr(src, "secteur", None)):
        abort(403)

    copie = Projet(nom=f"{src.nom} (reconduction)", secteur=src.secteur, description=src.description)
    db.session.add(copie)
    db.session.flush()

    # Liens ateliers
    for link in ProjetAtelier.query.filter_by(projet_id=src.id).all():
        db.session.add(ProjetAtelier(projet_id=copie.id, atelier_id=link.atelier_id))
    # Indicateurs (sans historique de valeurs)
    for ind in ProjetIndicateur.query.filter_by(projet_id=src.id).all():
        db.session.add(ProjetIndicateur(projet_id=copie.id, code=ind.code, label=ind.label,
                                        is_active=ind.is_active, params_json=ind.params_json))
    # Actions (sans liens ateliers spécifiques pour rester simple et sûr)
    for act in ProjetAction.query.filter_by(projet_id=src.id).all():
        db.session.add(ProjetAction(
            projet_id=copie.id, titre=act.titre, categorie=act.categorie, statut="prevue",
            referent=act.referent, lieu=act.lieu, public_vise=act.public_vise,
            territoire=act.territoire, objectifs=act.objectifs, description=act.description,
            partenaires_text=act.partenaires_text,
        ))
    # Jalons (réinitialisés à faire, sans date)
    for jal in ProjetJalon.query.filter_by(projet_id=src.id).all():
        db.session.add(ProjetJalon(projet_id=copie.id, libelle=jal.libelle, statut="a_faire", ordre=jal.ordre))

    db.session.commit()
    journaliser("projet.duplique", cible=f"projet#{copie.id}", details={"source": src.id, "nom": copie.nom})
    flash("Projet reconduit. Adapte le nom, les dates et le budget du nouvel exercice.", "success")
    return redirect(url_for("projets.projets_edit", projet_id=copie.id))


@bp.route("/projets/<int:projet_id>/jalons", methods=["GET", "POST"])
@login_required
@require_perm("projets:view")
def projet_jalons(projet_id: int):
    p = db.get_or_404(Projet, projet_id)
    if not can_access_secteur(p.secteur):
        abort(403)

    if request.method == "POST":
        if not can("projets:edit"):
            abort(403)
        action = request.form.get("action") or ""

        if action == "add":
            libelle = (request.form.get("libelle") or "").strip()
            if not libelle:
                flash("Le libellé du jalon est obligatoire.", "danger")
                return redirect(url_for("projets.projet_jalons", projet_id=p.id))
            d = (request.form.get("date_echeance") or "").strip()
            try:
                d_val = date.fromisoformat(d) if d else None
            except Exception:
                d_val = None
            try:
                ordre = int(request.form.get("ordre") or 0)
            except Exception:
                ordre = 0
            db.session.add(ProjetJalon(projet_id=p.id, libelle=libelle, date_echeance=d_val, ordre=ordre))
            db.session.commit()
            flash("Jalon ajouté.", "success")
            return redirect(url_for("projets.projet_jalons", projet_id=p.id))

        if action == "toggle":
            jid = int(request.form.get("jalon_id") or 0)
            jal = db.get_or_404(ProjetJalon, jid)
            if jal.projet_id != p.id:
                abort(400)
            jal.statut = "a_faire" if jal.statut == "fait" else "fait"
            db.session.commit()
            return redirect(url_for("projets.projet_jalons", projet_id=p.id))

        if action == "delete":
            jid = int(request.form.get("jalon_id") or 0)
            jal = db.get_or_404(ProjetJalon, jid)
            if jal.projet_id != p.id:
                abort(400)
            db.session.delete(jal)
            db.session.commit()
            flash("Jalon supprimé.", "warning")
            return redirect(url_for("projets.projet_jalons", projet_id=p.id))

        abort(400)

    jalons = (ProjetJalon.query.filter_by(projet_id=p.id)
              .order_by(ProjetJalon.statut.asc(), ProjetJalon.ordre.asc(), ProjetJalon.date_echeance.asc()).all())
    today = date.today()
    en_retard = sum(1 for j in jalons if j.statut != "fait" and j.date_echeance and j.date_echeance < today)
    return render_template("projets_jalons.html", projet=p, jalons=jalons, today=today,
                           en_retard=en_retard, can_edit=can("projets:edit"))

@bp.route("/projets/new", methods=["GET", "POST"])
@login_required
@require_perm("projets:edit")
def projets_new():
    secteurs = current_app.config.get("SECTEURS", [])

    if request.method == "POST":
        nom = (request.form.get("nom") or "").strip()
        secteur = (request.form.get("secteur") or "").strip()
        description = (request.form.get("description") or "").strip()

        if not can("scope:all_secteurs"):
            secteur = current_user.secteur_assigne

        if not nom or not secteur:
            flash("Le nom du projet et le secteur sont obligatoires.", "danger")
            return redirect(url_for("projets.projets_new"))

        if not can_see_secteur(secteur):
            abort(403)

        p = Projet(nom=nom, secteur=secteur, description=description)
        db.session.add(p)
        db.session.commit()

        flash("Le projet a bien été créé.", "success")
        return redirect(url_for("projets.projets_edit", projet_id=p.id))

    return render_template("projets_new.html", secteurs=secteurs)


@bp.route("/projets/<int:projet_id>", methods=["GET", "POST"])
@login_required
@require_perm("projets:view")
def projets_edit(projet_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)

    if request.method == "POST":
        if not can("projets:edit"):
            abort(403)
        action = request.form.get("action") or ""

        if action == "update":
            p.nom = (request.form.get("nom") or "").strip()
            p.description = (request.form.get("description") or "").strip()

            if not p.nom:
                flash("Le nom du projet est obligatoire.", "danger")
                return redirect(url_for("projets.projets_edit", projet_id=p.id))

            db.session.commit()
            flash("Les informations du projet ont bien été enregistrées.", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        if action == "update_competences":
            competence_ids = [int(cid) for cid in request.form.getlist("competence_ids") if cid.isdigit()]
            if competence_ids:
                p.competences = Competence.query.filter(Competence.id.in_(competence_ids)).all()
            else:
                p.competences = []
            db.session.commit()
            flash("Compétences du projet mises à jour.", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        if action == "upload_cr":
            if not can("projets:files"):
                abort(403)
            file = request.files.get("cr_file")
            if not file or not file.filename:
                flash("Aucun fichier.", "danger")
                return redirect(url_for("projets.projets_edit", projet_id=p.id))

            if not allowed_cr(file.filename):
                flash("Type autorisé : pdf/doc/docx/odt", "danger")
                return redirect(url_for("projets.projets_edit", projet_id=p.id))

            folder = ensure_projets_folder()
            safe_original = secure_filename(file.filename)
            ts = utcnow().strftime("%Y%m%d_%H%M%S")
            stored = secure_filename(f"P{p.id}_{ts}_{safe_original}")
            file.save(os.path.join(folder, stored))

            p.cr_filename = stored
            p.cr_original_name = safe_original
            db.session.commit()

            flash("Le compte-rendu a bien été importé.", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        if action == "toggle_subvention":
            if not can("subventions:link"):
                abort(403)
            sub_id = int(request.form.get("subvention_id") or 0)
            s = db.get_or_404(Subvention, sub_id)

            if s.secteur != p.secteur:
                abort(400)

            link = SubventionProjet.query.filter_by(projet_id=p.id, subvention_id=s.id).first()
            if link:
                db.session.delete(link)
                db.session.commit()
                flash("La subvention a été retirée du projet.", "warning")
            else:
                db.session.add(SubventionProjet(projet_id=p.id, subvention_id=s.id))
                db.session.commit()
                flash("La subvention a été rattachée au projet.", "success")

            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        # ---- Liens projet <-> ateliers ----
        if action == "toggle_atelier":
            if not can("ateliers:edit"):
                abort(403)
            atelier_id = int(request.form.get("atelier_id") or 0)
            a = db.get_or_404(AtelierActivite, atelier_id)
            if a.secteur != p.secteur or a.is_deleted:
                abort(400)

            link = ProjetAtelier.query.filter_by(projet_id=p.id, atelier_id=a.id).first()
            if link:
                db.session.delete(link)
                db.session.commit()
                flash("L'atelier a été retiré du projet.", "warning")
            else:
                db.session.add(ProjetAtelier(projet_id=p.id, atelier_id=a.id))
                db.session.commit()
                flash("L'atelier a été rattaché au projet.", "success")

            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        # ---- Indicateurs projet ----

        if action == "add_pack":
            pack = (request.form.get("pack") or "").strip()
            cfg = INDICATOR_PACKS.get(pack)
            if not cfg:
                flash("Le pack sélectionné est invalide.", "danger")
                return redirect(url_for("projets.projets_edit", projet_id=p.id))

            added = 0
            for code in cfg["codes"]:
                if code not in INDICATOR_TEMPLATES:
                    continue
                exists = ProjetIndicateur.query.filter_by(projet_id=p.id, code=code).first()
                if exists:
                    continue
                db.session.add(ProjetIndicateur(
                    projet_id=p.id,
                    code=code,
                    label=INDICATOR_TEMPLATES.get(code, code),
                    is_active=True,
                    params_json=None,
                ))
                added += 1
            db.session.commit()
            flash(f"Le pack a bien été ajouté ({added} indicateur(s)).", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        if action == "add_indicateur":
            code = (request.form.get("code") or "").strip()
            label = (request.form.get("label") or "").strip()
            if code not in INDICATOR_TEMPLATES:
                flash("L'indicateur sélectionné est invalide.", "danger")
                return redirect(url_for("projets.projets_edit", projet_id=p.id))
            if not label:
                label = INDICATOR_TEMPLATES[code]

            exists = ProjetIndicateur.query.filter_by(projet_id=p.id, code=code).first()
            if exists:
                flash("Cet indicateur est déjà présent sur ce projet.", "warning")
                return redirect(url_for("projets.projets_edit", projet_id=p.id))

            db.session.add(ProjetIndicateur(projet_id=p.id, code=code, label=label, is_active=True))
            db.session.commit()
            flash("L'indicateur a bien été ajouté.", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        if action == "toggle_indicateur":
            indic_id = int(request.form.get("indicateur_id") or 0)
            ind = db.get_or_404(ProjetIndicateur, indic_id)
            if ind.projet_id != p.id:
                abort(400)
            ind.is_active = not bool(ind.is_active)
            db.session.commit()
            flash("L'indicateur a bien été mis à jour.", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        if action == "save_indicateur":
            indic_id = int(request.form.get("indicateur_id") or 0)
            ind = db.get_or_404(ProjetIndicateur, indic_id)
            if ind.projet_id != p.id:
                abort(400)

            # label editable (optionnel)
            label = (request.form.get("label") or "").strip()
            if label:
                ind.label = label

            period = (request.form.get("period") or "context").strip()
            if period not in PERIOD_CHOICES:
                period = "context"

            target_raw = (request.form.get("target") or "").strip().replace(",", ".")
            target = None
            if target_raw:
                try:
                    target = float(target_raw)
                except ValueError:
                    target = None

            target_op = (request.form.get("target_op") or "ge").strip()
            if target_op not in TARGET_OP_CHOICES:
                target_op = "ge"

            atelier_id_raw = (request.form.get("atelier_id") or "").strip()
            atelier_id = None
            if atelier_id_raw:
                try:
                    atelier_id = int(atelier_id_raw)
                except ValueError:
                    atelier_id = None

            # bornes custom
            start = (request.form.get("start") or "").strip()
            end = (request.form.get("end") or "").strip()

            params = ind.params()
            params.update({
                "period": period,
                "target": target,
                "target_op": target_op,
                "atelier_id": atelier_id,
                "start": start if period == "custom" else None,
                "end": end if period == "custom" else None,
            })
            # nettoyage
            if params.get("atelier_id") is None:
                params.pop("atelier_id", None)
            if params.get("target") is None:
                params.pop("target", None)
            if period != "custom":
                params.pop("start", None)
                params.pop("end", None)

            ind.params_json = json.dumps(params, ensure_ascii=False)
            db.session.commit()
            flash("Les paramètres de l'indicateur ont bien été enregistrés.", "success")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))


        if action == "delete_indicateur":
            indic_id = int(request.form.get("indicateur_id") or 0)
            ind = db.get_or_404(ProjetIndicateur, indic_id)
            if ind.projet_id != p.id:
                abort(400)
            db.session.delete(ind)
            db.session.commit()
            flash("L'indicateur a été supprimé.", "warning")
            return redirect(url_for("projets.projets_edit", projet_id=p.id))

        abort(400)

    # ----- GET (lists) -----
    subs_q = Subvention.query.filter_by(est_archive=False).filter(Subvention.secteur == p.secteur)
    subs = subs_q.order_by(Subvention.annee_exercice.desc(), Subvention.nom.asc()).all()
    linked_subs = set(sp.subvention_id for sp in p.subventions)

    ateliers = AtelierActivite.query.filter_by(secteur=p.secteur, is_deleted=False).order_by(AtelierActivite.nom.asc()).all()
    linked_ateliers = set(link.atelier_id for link in ProjetAtelier.query.filter_by(projet_id=p.id).all())

    indicateurs = ProjetIndicateur.query.filter_by(projet_id=p.id).order_by(ProjetIndicateur.created_at.asc()).all()
    referentiels = Referentiel.query.order_by(Referentiel.nom.asc()).all()
    selected_competences = {c.id for c in p.competences}

    return render_template(
        "projets_edit.html",
        projet=p,
        subs=subs,
        linked=linked_subs,
        ateliers=ateliers,
        linked_ateliers=linked_ateliers,
        indicateurs=indicateurs,
        indicator_templates=INDICATOR_TEMPLATES,
        indicator_metric_groups=INDICATOR_METRIC_GROUPS,
        indicator_packs=INDICATOR_PACKS,
        period_choices=PERIOD_CHOICES,
        target_op_choices=TARGET_OP_CHOICES,
        referentiels=referentiels,
        selected_competences=selected_competences,
    )


@bp.route("/projets/<int:projet_id>/delete", methods=["POST"])
@login_required
@require_perm('projets:delete')
def projets_delete(projet_id: int):
    """Suppression définitive d'un projet (et de ses liens).

    On fait du *delete applicatif* (compatible SQLite/Postgres) :
    - objectifs + table pivot objectif_competence
    - liens projet_atelier
    - liens projet_competence (table pivot)
    - indicateurs
    - liens projet<->subvention (SubventionProjet)
    Puis on supprime le projet.

    ⚠️ Non réversible. (On pourrait faire un soft-delete plus tard si tu veux.)
    """
    projet = db.get_or_404(Projet, projet_id)

    if not can_access_secteur(getattr(projet, "secteur", None)):
        flash("Accès refusé.", "danger")
        return redirect(url_for("projets.projets_list"))

    try:
        # 1) Objectifs liés au projet + pivot objectif_competence
        objectif_ids = [o.id for o in Objectif.query.filter_by(projet_id=projet.id).all()]
        if objectif_ids:
            db.session.execute(
                objectif_competence.delete().where(objectif_competence.c.objectif_id.in_(objectif_ids))
            )
            # suppression des objectifs (les enfants sont aussi dans la liste via projet_id)
            Objectif.query.filter(Objectif.id.in_(objectif_ids)).delete(synchronize_session=False)

        # 2) Liens projet/ateliers
        ProjetAtelier.query.filter_by(projet_id=projet.id).delete(synchronize_session=False)

        # 3) Liens projet/compétences (table pivot)
        db.session.execute(
            projet_competence.delete().where(projet_competence.c.projet_id == projet.id)
        )

        # 4) Indicateurs
        ProjetIndicateur.query.filter_by(projet_id=projet.id).delete(synchronize_session=False)
        ProjetJournalEntry.query.filter_by(projet_id=projet.id).delete(synchronize_session=False)
        action_ids = [a.id for a in ProjetAction.query.filter_by(projet_id=projet.id).all()]
        if action_ids:
            ProjetActionAtelier.query.filter(ProjetActionAtelier.action_id.in_(action_ids)).delete(synchronize_session=False)
            ProjetAction.query.filter(ProjetAction.id.in_(action_ids)).delete(synchronize_session=False)

        # 5) Liens projet/subventions
        SubventionProjet.query.filter_by(projet_id=projet.id).delete(synchronize_session=False)

        # 6) Fichier CR (si présent) — suppression physique seulement après succès BDD
        cr_relpath = None
        try:
            if projet.cr_filename:
                cr_relpath = os.path.join(current_app.instance_path, "cr_projets", projet.cr_filename)
        except Exception:
            cr_relpath = None

        projet_nom = projet.nom
        db.session.delete(projet)
        supprime = commit_delete(
            f"le projet « {projet.nom} »",
            "Projet supprimé définitivement.",
            success_category="warning",
            blocked_message=f"Impossible de supprimer le projet « {projet.nom} » : il est encore utilisé ailleurs.",
        )
        if supprime:
            journaliser("projet.delete", cible=f"projet#{projet_id}", details={"nom": projet_nom})
            if cr_relpath:
                try:
                    if os.path.exists(cr_relpath):
                        os.remove(cr_relpath)
                except Exception:
                    pass
    except Exception as e:
        db.session.rollback()
        flash(f"Suppression impossible : {e}", "danger")

    return redirect(url_for("projets.projets_list"))


@bp.route("/projets/cr/<int:projet_id>/download")
@login_required
@require_perm("projets:files")
def projets_cr_download(projet_id):
    p = db.get_or_404(Projet, projet_id)
    if not can_see_secteur(p.secteur):
        abort(403)

    if not p.cr_filename:
        abort(404)

    ensure_projets_folder()
    return send_media_file(media_relpath("projets", p.cr_filename), as_attachment=True, download_name=(p.cr_original_name or p.cr_filename))

