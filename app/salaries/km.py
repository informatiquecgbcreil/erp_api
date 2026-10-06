"""Frais kilométriques : saisie signée, suivi d'équipe, barèmes, passage en dépense."""
from __future__ import annotations

import os
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import BaremeKilometrique, FraisKilometrique, Salarie
from app.rbac import can, require_perm
from app.services import espace_salarie as es
from app.services import frais_km as fk
from app.services.audit import journaliser
from app.services.storage import send_media_file
from app.utils.dates import utcnow

from . import bp
from .commun import erreur, ma_fiche_ou_explication

TAILLE_MAX = 10 * 1024 * 1024
EXTENSIONS_JUSTIFICATIF = {"pdf", "jpg", "jpeg", "png", "heic", "webp"}


def _url_mes():
    return url_for("salaries.mes_frais_km")


def _lire_formulaire(form) -> dict:
    """Champs du trajet ; lève ValueError avec un message lisible."""
    jour = es.parse_date(form.get("date_trajet"))
    if jour is None:
        raise ValueError("Indique la date du trajet.")
    if jour > date.today():
        raise ValueError("Le trajet ne peut pas être dans le futur.")
    type_vehicule = (form.get("type_vehicule") or "").strip()
    if type_vehicule not in fk.TYPES_VEHICULE:
        raise ValueError("Choisis le type de véhicule.")
    try:
        puissance = int(form.get("puissance_fiscale") or 0)
        distance = int(round(float((form.get("distance_km") or "0").replace(",", "."))))
    except ValueError:
        raise ValueError("Puissance ou distance illisible.") from None
    if puissance <= 0:
        raise ValueError("Indique la puissance fiscale (CV).")
    if distance <= 0:
        raise ValueError("La distance doit être d'au moins 1 km.")
    motif = (form.get("motif") or "").strip()
    if not motif:
        raise ValueError("Indique le motif du déplacement.")
    return {
        "date_trajet": jour, "type_vehicule": type_vehicule, "puissance_fiscale": puissance,
        "distance_km": distance, "electrique": form.get("electrique") == "1", "motif": motif[:500],
        "lien": (form.get("lien") or "").strip(),
    }


def _vehicule_habituel(salarie_id: int) -> dict:
    """Le véhicule de la dernière note, pour pré-remplir le formulaire."""
    derniere = (FraisKilometrique.query.filter_by(salarie_id=salarie_id)
                .order_by(FraisKilometrique.id.desc()).first())
    if derniere is None:
        return {}
    return {"type_vehicule": derniere.type_vehicule, "puissance_fiscale": derniere.puissance_fiscale,
            "electrique": "1" if derniere.electrique else ""}


@bp.route("/frais-km", methods=["GET", "POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def mes_frais_km(salarie):
    annee = request.args.get("annee", type=int) or date.today().year
    notes = (FraisKilometrique.query.filter_by(salarie_id=salarie.id, annee=annee)
             .order_by(FraisKilometrique.date_trajet.desc(), FraisKilometrique.id.desc()).all())
    saisie = dict(_vehicule_habituel(salarie.id))
    saisie.setdefault("date_trajet", date.today().isoformat())
    apercu = None
    if request.method == "POST":
        saisie.update({k: v for k, v in request.form.items() if k != "csrf_token"})
        try:
            champs = _lire_formulaire(request.form)
            calcul = fk.calculer(salarie.id, champs["date_trajet"].year, champs["type_vehicule"],
                                 champs["puissance_fiscale"], champs["distance_km"], champs["electrique"])
            es.lien_agenda_valide(current_user, champs["date_trajet"], champs["lien"])
            apercu = {"calcul": calcul, "champs": champs}
        except ValueError as exc:
            flash(str(exc), "danger")
    jour = es.parse_date(saisie.get("date_trajet")) or date.today()
    return render_template(
        "salaries/mes_frais_km.html",
        salarie=salarie, notes=notes, annee=annee, saisie=saisie, apercu=apercu,
        resume=es.resume_frais_km(salarie.id, annee), vehicules=fk.TYPES_VEHICULE,
        combinaisons=fk.combinaisons_disponibles(), suggestions=es.evenements_du_jour(current_user, jour),
        fk=fk, es=es,
    )


@bp.route("/frais-km/enregistrer", methods=["POST"])
@login_required
@require_perm("salarie:espace")
@ma_fiche_ou_explication
def enregistrer_frais_km(salarie):
    try:
        champs = _lire_formulaire(request.form)
        session_id, _ = es.lien_agenda_valide(current_user, champs["date_trajet"], champs["lien"])
        # Recalcul serveur : le montant affiché à l'écran n'est jamais repris tel quel.
        calcul = fk.calculer(salarie.id, champs["date_trajet"].year, champs["type_vehicule"],
                             champs["puissance_fiscale"], champs["distance_km"], champs["electrique"])
    except ValueError as exc:
        return erreur(str(exc), _url_mes())
    signature_data = (request.form.get("signature_data") or "").strip()
    je_signe = request.form.get("je_signe") == "1"
    if not signature_data and not je_signe:
        return erreur("Signe la note (ou coche « je signe » si le cadre ne fonctionne pas).", _url_mes())

    justificatif = request.files.get("justificatif")
    chemin_justif = nom_justif = None
    if justificatif and justificatif.filename:
        extension = justificatif.filename.rsplit(".", 1)[-1].lower() if "." in justificatif.filename else ""
        if extension not in EXTENSIONS_JUSTIFICATIF:
            return erreur("Justificatif : PDF ou photo (JPG, PNG) uniquement.", _url_mes())
        justificatif.stream.seek(0, os.SEEK_END)
        taille = justificatif.stream.tell()
        justificatif.stream.seek(0)
        if taille > TAILLE_MAX:
            return erreur("Justificatif trop lourd (10 Mo au plus).", _url_mes())
        dossier = es.dossier_rh("frais_km", str(salarie.id))
        nom_stocke = f"{utcnow().strftime('%Y%m%d%H%M%S%f')}_{secure_filename(justificatif.filename) or 'justificatif'}"
        justificatif.save(str(dossier / nom_stocke))
        chemin_justif = es.chemin_relatif(dossier / nom_stocke)
        nom_justif = justificatif.filename[:300]

    note = FraisKilometrique(
        salarie_id=salarie.id, saisi_par_user_id=current_user.id, date_trajet=champs["date_trajet"],
        annee=champs["date_trajet"].year, annee_bareme=calcul.annee_bareme,
        type_vehicule=champs["type_vehicule"], puissance_fiscale=champs["puissance_fiscale"],
        electrique=champs["electrique"], distance_km=champs["distance_km"], cumul_km_avant=calcul.cumul_avant,
        secteur=salarie.secteur,
        motif=champs["motif"], montant_centimes=calcul.montant_centimes,
        taux_millieme=calcul.ligne.taux_millieme, forfait_centimes=calcul.ligne.forfait_centimes,
        bonus_electrique_pct=calcul.ligne.bonus_electrique_pct if champs["electrique"] else 0,
        justificatif_chemin=chemin_justif, justificatif_nom=nom_justif,
        statut="signee", signee_le=utcnow(), session_id=session_id,
    )
    db.session.add(note)
    db.session.flush()
    try:
        sig = es.enregistrer_signature(signature_data, contexte="frais_km_signature",
                                       objet_type="frais_kilometrique", objet_id=note.id,
                                       vide_autorise=je_signe)
    except ValueError as exc:
        db.session.rollback()
        return erreur(str(exc), _url_mes())
    note.signature_id = sig.id
    db.session.commit()
    journaliser("rh.frais_km", cible=f"frais_kilometrique#{note.id}",
                details={"km": note.distance_km, "montant_centimes": note.montant_centimes})
    flash(f"Note signée et enregistrée : {es.format_euros(note.montant_centimes)}.", "success")
    return redirect(_url_mes())


@bp.route("/frais-km/<int:note_id>/justificatif")
@login_required
@require_perm("salarie:espace")
def justificatif_frais_km(note_id: int):
    note = db.get_or_404(FraisKilometrique, note_id)
    if note.salarie.user_id != current_user.id and not can("frais_km:suivi"):
        abort(403)
    if not note.justificatif_chemin:
        abort(404)
    reponse = send_media_file(note.justificatif_chemin, as_attachment=True,
                              download_name=note.justificatif_nom or "justificatif")
    reponse.headers["Cache-Control"] = "private, no-store"
    return reponse


# ---------------------------------------------------------------------------
# Suivi de l'équipe et passage en dépense
# ---------------------------------------------------------------------------

def _lignes_suggerees(note, lignes):
    """La ligne de charge la plus probable : secteur du salarié, compte 625 (déplacements)."""
    secteur = (note.salarie.secteur or "").strip()

    def score(ligne):
        sub = ligne.source_sub
        return (
            1 if sub and sub.annee_exercice == note.annee else 0,
            1 if sub and secteur and sub.secteur == secteur else 0,
            1 if (ligne.compte or "").startswith("625") else 0,
            1 if (ligne.compte or "").startswith("62") else 0,
        )
    return max(lignes, key=score) if lignes else None


@bp.route("/equipe/frais-km")
@login_required
@require_perm("frais_km:suivi")
def equipe_frais_km():
    f_salarie = (request.args.get("salarie_id") or "").strip()
    annee = request.args.get("annee", type=int) or date.today().year
    du = es.parse_date(request.args.get("du"))
    au = es.parse_date(request.args.get("au"))
    f_depense = (request.args.get("depense") or "").strip()

    q = FraisKilometrique.query.filter(FraisKilometrique.annee == annee)
    if f_salarie.isdigit():
        q = q.filter(FraisKilometrique.salarie_id == int(f_salarie))
    if du:
        q = q.filter(FraisKilometrique.date_trajet >= du)
    if au:
        q = q.filter(FraisKilometrique.date_trajet <= au)
    if f_depense == "non":
        q = q.filter(FraisKilometrique.depense_id.is_(None))
    elif f_depense == "oui":
        q = q.filter(FraisKilometrique.depense_id.isnot(None))
    notes = q.order_by(FraisKilometrique.date_trajet.desc(), FraisKilometrique.id.desc()).limit(500).all()

    lignes, suggestions = [], {}
    peut_depense = can("depenses:create")
    if peut_depense:
        from app.budget.routes import _visible_charge_lines_for_depense
        lignes = _visible_charge_lines_for_depense()
        for n in notes:
            if n.depense_id is None:
                ligne = _lignes_suggerees(n, lignes)
                suggestions[n.id] = ligne.id if ligne else None
    annees = [a for (a,) in db.session.query(FraisKilometrique.annee).distinct()
              .order_by(FraisKilometrique.annee.desc()).all()] or [annee]
    return render_template(
        "salaries/equipe_frais_km.html",
        notes=notes, annee=annee, annees=annees, f_salarie=f_salarie, du=du, au=au, f_depense=f_depense,
        total_km=sum(n.distance_km for n in notes), total=sum(n.montant_centimes for n in notes),
        salaries=Salarie.query.order_by(Salarie.nom.asc(), Salarie.prenom.asc()).all(),
        lignes=lignes, suggestions=suggestions, peut_depense=peut_depense, fk=fk, es=es,
    )


@bp.route("/equipe/frais-km/<int:note_id>/depense", methods=["POST"])
@login_required
@require_perm("frais_km:suivi")
@require_perm("depenses:create")
def frais_km_en_depense(note_id: int):
    """Crée la dépense correspondante sur une ligne de charge, et relie les deux."""
    from app.budget.routes import (_create_affectations_for_depense, _validate_rows_capacity,
                                   can_see_secteur)
    from app.models import Depense, LigneBudget

    note = db.get_or_404(FraisKilometrique, note_id)
    url = url_for("salaries.equipe_frais_km", annee=note.annee)
    if note.depense_id:
        return erreur("Cette note est déjà passée en dépense.", url)
    ligne = db.session.get(LigneBudget, request.form.get("ligne_budget_id", type=int) or 0)
    if ligne is None or getattr(ligne, "nature", "charge") != "charge" or ligne.source_sub is None:
        return erreur("Choisis une ligne de charge d'un financement.", url)
    if not can_see_secteur(ligne.source_sub.secteur):
        abort(403)
    montant = round(note.montant_centimes / 100, 2)
    lignes_affectation = [{"source_type": "subvention", "subvention_id": ligne.source_sub.id,
                           "ligne_budget_id": ligne.id, "libelle_source": None, "montant": montant,
                           "commentaire": f"Frais kilométriques n° {note.id}"}]
    ok, message = _validate_rows_capacity(lignes_affectation)
    if not ok:
        return erreur(message, url)
    depense = Depense(
        ligne_budget_id=ligne.id,
        libelle=f"Frais kilométriques — {note.salarie.nom_complet} — {note.motif}"[:255],
        montant=montant,
        fournisseur=note.salarie.nom_complet[:180],
        reference_piece=f"FK-{note.id}",
        type_depense="Déplacements",
        date_paiement=None,
    )
    db.session.add(depense)
    db.session.flush()
    _create_affectations_for_depense(depense, lignes_affectation)
    note.depense_id = depense.id
    db.session.commit()
    journaliser("rh.frais_km_depense", cible=f"frais_kilometrique#{note.id}", details={"depense_id": depense.id})
    flash(f"Dépense créée sur « {ligne.libelle} » ({es.format_euros(note.montant_centimes)}).", "success")
    return redirect(url)


# ---------------------------------------------------------------------------
# Barèmes
# ---------------------------------------------------------------------------

def _url_baremes():
    return url_for("salaries.baremes_km")


@bp.route("/frais-km/baremes", methods=["GET", "POST"])
@login_required
@require_perm("frais_km:baremes")
def baremes_km():
    if request.method == "POST":
        action = request.form.get("action") or "ajouter"
        try:
            if action == "dupliquer":
                return _dupliquer_bareme()
            if action == "bonus":
                return _appliquer_bonus()
            return _ajouter_ligne()
        except ValueError as exc:
            db.session.rollback()
            return erreur(str(exc), _url_baremes())
    lignes = (BaremeKilometrique.query.order_by(
        BaremeKilometrique.annee.desc(), BaremeKilometrique.type_vehicule.asc(),
        BaremeKilometrique.puissance_fiscale.asc(), BaremeKilometrique.km_de.asc()).all())
    annees = sorted({ligne.annee for ligne in lignes}, reverse=True)
    return render_template("salaries/baremes_km.html", lignes=lignes, annees=annees,
                           vehicules=fk.TYPES_VEHICULE, tranches=fk.TRANCHES_USUELLES,
                           annee_courante=date.today().year, fk=fk, es=es)


def _entier(nom: str, *, defaut=None, obligatoire=True) -> int | None:
    brut = (request.form.get(nom) or "").strip()
    if not brut:
        if obligatoire and defaut is None:
            raise ValueError(f"Champ manquant : {nom.replace('_', ' ')}.")
        return defaut
    try:
        return int(brut)
    except ValueError:
        raise ValueError(f"Nombre entier attendu : {nom.replace('_', ' ')}.") from None


def _ajouter_ligne():
    annee = _entier("annee")
    type_vehicule = (request.form.get("type_vehicule") or "").strip()
    if type_vehicule not in fk.TYPES_VEHICULE:
        raise ValueError("Type de véhicule inconnu.")
    puissance = _entier("puissance_fiscale")
    tranche = (request.form.get("tranche") or "").strip()
    if tranche in fk.TRANCHES_USUELLES:
        km_de, km_a = fk.TRANCHES_USUELLES[tranche]
    else:
        km_de = _entier("km_de", defaut=0)
        km_a = _entier("km_a", obligatoire=False)
    if km_a is not None and km_a < km_de:
        raise ValueError("La tranche se termine avant de commencer.")
    taux = fk.parse_taux_millieme(request.form.get("taux"))
    forfait = es.parse_euros(request.form.get("forfait"))
    bonus = _entier("bonus_electrique_pct", defaut=0)
    ligne = BaremeKilometrique.query.filter_by(annee=annee, type_vehicule=type_vehicule,
                                               puissance_fiscale=puissance, km_de=km_de, km_a=km_a).first()
    nouvelle = ligne is None
    if nouvelle:
        ligne = BaremeKilometrique(annee=annee, type_vehicule=type_vehicule, puissance_fiscale=puissance,
                                   km_de=km_de, km_a=km_a)
        db.session.add(ligne)
    ligne.taux_millieme, ligne.forfait_centimes, ligne.bonus_electrique_pct = taux, forfait, bonus
    db.session.commit()
    journaliser("rh.bareme_km", cible=f"bareme_kilometrique#{ligne.id}",
                details={"annee": annee, "vehicule": type_vehicule, "cv": puissance, "nouvelle": nouvelle})
    flash("Ligne de barème ajoutée." if nouvelle else "Ligne de barème mise à jour (elle existait déjà).", "success")
    return redirect(_url_baremes())


def _dupliquer_bareme():
    source, cible = _entier("annee_source"), _entier("annee_cible")
    if source == cible:
        raise ValueError("Choisis deux années différentes.")
    type_vehicule = (request.form.get("type_vehicule") or "").strip() or None
    bonus = _entier("bonus_electrique_pct", obligatoire=False)
    q = BaremeKilometrique.query.filter_by(annee=source)
    if type_vehicule:
        q = q.filter_by(type_vehicule=type_vehicule)
    crees = deja = 0
    for ligne in q.all():
        if BaremeKilometrique.query.filter_by(annee=cible, type_vehicule=ligne.type_vehicule,
                                              puissance_fiscale=ligne.puissance_fiscale,
                                              km_de=ligne.km_de, km_a=ligne.km_a).first():
            deja += 1
            continue
        db.session.add(BaremeKilometrique(
            annee=cible, type_vehicule=ligne.type_vehicule, puissance_fiscale=ligne.puissance_fiscale,
            km_de=ligne.km_de, km_a=ligne.km_a, taux_millieme=ligne.taux_millieme,
            forfait_centimes=ligne.forfait_centimes,
            bonus_electrique_pct=ligne.bonus_electrique_pct if bonus is None else bonus,
        ))
        crees += 1
    if not crees and not deja:
        raise ValueError(f"Aucune ligne de barème en {source}.")
    db.session.commit()
    journaliser("rh.bareme_km_duplique", cible=f"bareme {source}->{cible}", details={"crees": crees, "deja": deja})
    flash(f"Barème {source} recopié sur {cible} : {crees} ligne(s) ajoutée(s), {deja} déjà présente(s).", "success")
    return redirect(_url_baremes())


def _appliquer_bonus():
    annee = _entier("annee")
    bonus = _entier("bonus_electrique_pct", defaut=0)
    type_vehicule = (request.form.get("type_vehicule") or "").strip() or None
    q = BaremeKilometrique.query.filter_by(annee=annee)
    if type_vehicule:
        q = q.filter_by(type_vehicule=type_vehicule)
    lignes = q.all()
    for ligne in lignes:
        ligne.bonus_electrique_pct = bonus
    db.session.commit()
    journaliser("rh.bareme_km_bonus", cible=f"bareme {annee}", details={"bonus": bonus, "lignes": len(lignes)})
    flash(f"Majoration électrique de {bonus} % appliquée à {len(lignes)} ligne(s) de {annee}.", "success")
    return redirect(_url_baremes())


@bp.route("/frais-km/baremes/<int:ligne_id>/supprimer", methods=["POST"])
@login_required
@require_perm("frais_km:baremes")
def supprimer_ligne_bareme(ligne_id: int):
    ligne = db.get_or_404(BaremeKilometrique, ligne_id)
    db.session.delete(ligne)
    db.session.commit()
    journaliser("rh.bareme_km_suppr", cible=f"bareme_kilometrique#{ligne_id}")
    flash("Ligne de barème supprimée (les notes déjà signées gardent leur montant).", "success")
    return redirect(_url_baremes())
