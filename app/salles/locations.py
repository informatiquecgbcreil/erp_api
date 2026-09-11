"""Écrans des mises à disposition : preneurs, grille tarifaire, réservations.

Séparé de ``routes.py`` qui gère le référentiel des lieux : ce sont deux
métiers différents, et le fichier commençait à ressembler à un grenier.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    CONDITIONS_MAJORATION,
    CONDITIONS_MAJORATION_LABELS,
    STATUTS_RESERVATION,
    STATUTS_RESERVATION_LABELS,
    UNITES_PRESTATION,
    UNITES_PRESTATION_LABELS,
    UNITES_TARIF,
    UNITES_TARIF_LABELS,
    CategoriePreneur,
    Espace,
    MajorationSalle,
    Preneur,
    PrestationSalle,
    Reservation,
    ReservationPrestation,
    TarifSalle,
)
from app.rbac import require_perm
from app.services.audit import journaliser
from app.services.reservations import (
    appliquer_dates,
    bloquants,
    controles,
    dates_recurrentes,
    purger_options_expirees,
    recalculer,
    reference_unique,
    synchroniser_statut_occupations,
)
from app.services.salles import SalleErreur, espaces_louables, normaliser_plage
from app.services.tarifs_salles import grille_en_vigueur
from app.utils.delete_guard import commit_delete

from . import bp
from .routes import _case, _date, _decimal, _entier, _texte

#: Catégories proposées à l'installation. Point de départ discutable et
#: modifiable — chaque structure a les siennes.
CATEGORIES_DEPART = [
    ("asso_territoire", "Association du territoire", False),
    ("asso_exterieure", "Association hors territoire", False),
    ("entreprise", "Entreprise", False),
    ("particulier", "Particulier", False),
    ("service_municipal", "Service municipal", False),
    ("partenaire", "Partenaire conventionné", True),
    ("interne", "Usage interne", True),
]


# ---------------------------------------------------------------------------
# Catégories de preneurs
# ---------------------------------------------------------------------------

@bp.route("/categories", methods=["GET", "POST"])
@login_required
@require_perm("salles:tarifs")
def categories():
    """Le second axe de la grille : qui loue, et donc à quel prix."""
    if request.method == "POST":
        libelle = _texte("libelle")
        if not libelle:
            flash("Le libellé est obligatoire.", "danger")
        else:
            import re
            import unicodedata

            base = unicodedata.normalize("NFKD", libelle)
            base = "".join(c for c in base if not unicodedata.combining(c))
            code = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:40] or "categorie"
            n, candidat = 1, code
            while CategoriePreneur.query.filter_by(code=candidat).first():
                n += 1
                candidat = f"{code}_{n}"[:40]

            db.session.add(CategoriePreneur(
                code=candidat, libelle=libelle,
                description=_texte("description") or None,
                gratuit_par_defaut=_case("gratuit_par_defaut"),
                ordre=(CategoriePreneur.query.count() + 1) * 10,
            ))
            db.session.commit()
            journaliser("salles.categorie", cible=libelle)
            flash(f"Catégorie « {libelle} » ajoutée.", "success")
        return redirect(url_for("salles.categories"))

    return render_template(
        "salles/categories.html",
        categories=CategoriePreneur.query.order_by(CategoriePreneur.ordre, CategoriePreneur.libelle).all(),
        depart=CATEGORIES_DEPART,
    )


@bp.route("/categories/installer", methods=["POST"])
@login_required
@require_perm("salles:tarifs")
def categories_installer():
    if CategoriePreneur.query.count():
        flash("Des catégories existent déjà : rien n'a été ajouté.", "warning")
        return redirect(url_for("salles.categories"))
    for rang, (code, libelle, gratuit) in enumerate(CATEGORIES_DEPART):
        db.session.add(CategoriePreneur(
            code=code, libelle=libelle, gratuit_par_defaut=gratuit, ordre=rang * 10,
        ))
    db.session.commit()
    journaliser("salles.categories_depart", details={"nombre": len(CATEGORIES_DEPART)})
    flash(f"{len(CATEGORIES_DEPART)} catégories créées. Retire celles qui ne te servent pas.", "success")
    return redirect(url_for("salles.categories"))


@bp.route("/categories/<int:categorie_id>/basculer", methods=["POST"])
@login_required
@require_perm("salles:tarifs")
def categorie_basculer(categorie_id: int):
    categorie = CategoriePreneur.query.get_or_404(categorie_id)
    categorie.actif = not categorie.actif
    db.session.commit()
    flash(f"« {categorie.libelle} » {'réactivée' if categorie.actif else 'désactivée'}.", "success")
    return redirect(url_for("salles.categories"))


@bp.route("/categories/<int:categorie_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:tarifs")
def categorie_supprimer(categorie_id: int):
    categorie = CategoriePreneur.query.get_or_404(categorie_id)
    libelle = categorie.libelle
    db.session.delete(categorie)
    commit_delete(
        "cette catégorie",
        f"Catégorie « {libelle} » supprimée, avec ses tarifs.",
        blocked_message=f"« {libelle} » est utilisée par des réservations : désactive-la plutôt.",
    )
    return redirect(url_for("salles.categories"))


# ---------------------------------------------------------------------------
# Grille tarifaire
# ---------------------------------------------------------------------------

@bp.route("/tarifs", methods=["GET", "POST"])
@login_required
@require_perm("salles:tarifs")
def tarifs():
    """Salle × unité × catégorie, historisé par date d'effet."""
    salles = espaces_louables()
    categories = CategoriePreneur.query.filter_by(actif=True).order_by(
        CategoriePreneur.ordre, CategoriePreneur.libelle
    ).all()

    if request.method == "POST":
        espace_id = _entier("espace_id")
        categorie_id = _entier("categorie_id")
        date_debut = _date("date_debut") or date.today()
        enregistres = 0
        for unite in UNITES_TARIF:
            montant = _decimal(f"montant_{unite}")
            if montant is None:
                continue
            db.session.add(TarifSalle(
                espace_id=espace_id, categorie_id=categorie_id, unite=unite,
                montant=montant, date_debut=date_debut,
                commentaire=_texte("commentaire") or None,
                created_by_user_id=getattr(current_user, "id", None),
            ))
            enregistres += 1
        if not (espace_id and categorie_id and enregistres):
            flash("Choisis une salle, une catégorie et au moins un montant.", "danger")
        else:
            db.session.commit()
            journaliser("salles.tarifs", details={"espace": espace_id, "lignes": enregistres})
            flash(
                f"{enregistres} tarif(s) enregistré(s) à effet du {date_debut.strftime('%d/%m/%Y')}. "
                "Les réservations déjà calculées ne bougent pas.",
                "success",
            )
        return redirect(url_for("salles.tarifs", espace_id=espace_id, categorie_id=categorie_id))

    espace_id = request.args.get("espace_id", type=int) or (salles[0].id if salles else None)
    jour = _date_reference()

    # La grille du jour, lisible d'un coup d'œil : une ligne par catégorie.
    lignes = []
    if espace_id:
        for categorie in categories:
            lignes.append({
                "categorie": categorie,
                "montants": grille_en_vigueur(espace_id, categorie.id, jour),
            })

    historique = []
    if espace_id:
        historique = (
            TarifSalle.query
            .filter(TarifSalle.espace_id == espace_id)
            .order_by(TarifSalle.date_debut.desc(), TarifSalle.id.desc())
            .limit(60).all()
        )

    return render_template(
        "salles/tarifs.html",
        salles=salles, categories=categories, espace_id=espace_id,
        espace=Espace.query.get(espace_id) if espace_id else None,
        lignes=lignes, historique=historique, jour=jour,
        unites=UNITES_TARIF, unites_labels=UNITES_TARIF_LABELS,
        aujourdhui=date.today(),
    )


def _date_reference():
    brut = (request.args.get("au") or "").strip()
    if brut:
        try:
            return date.fromisoformat(brut)
        except ValueError:
            pass
    return date.today()


@bp.route("/tarifs/<int:tarif_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:tarifs")
def tarif_supprimer(tarif_id: int):
    tarif = TarifSalle.query.get_or_404(tarif_id)
    espace_id = tarif.espace_id
    db.session.delete(tarif)
    db.session.commit()
    flash("Ligne de tarif supprimée.", "success")
    return redirect(url_for("salles.tarifs", espace_id=espace_id))


# ---------------------------------------------------------------------------
# Prestations annexes et majorations
# ---------------------------------------------------------------------------

@bp.route("/prestations", methods=["GET", "POST"])
@login_required
@require_perm("salles:tarifs")
def prestations():
    """Vidéoprojecteur, sono, ménage, mise en place des tables, café."""
    if request.method == "POST":
        cible = _texte("cible")
        if cible == "prestation":
            libelle = _texte("libelle")
            if not libelle:
                flash("Le libellé est obligatoire.", "danger")
            else:
                db.session.add(PrestationSalle(
                    libelle=libelle, description=_texte("description") or None,
                    montant=_decimal("montant") or 0.0,
                    unite=_texte("unite", "forfait"),
                    ordre=(PrestationSalle.query.count() + 1) * 10,
                ))
                db.session.commit()
                flash(f"Prestation « {libelle} » ajoutée.", "success")
        elif cible == "majoration":
            libelle = _texte("libelle_majoration")
            pourcentage = _decimal("pourcentage")
            montant_fixe = _decimal("montant_fixe")
            if not libelle:
                flash("Le libellé de la majoration est obligatoire.", "danger")
            elif not pourcentage and not montant_fixe:
                flash("Une majoration sans pourcentage ni montant ne majore rien.", "danger")
            elif pourcentage and montant_fixe:
                flash(
                    "Choisis un pourcentage OU un montant fixe, pas les deux : "
                    "une règle qu'on ne sait pas dire en une phrase finit en litige.",
                    "danger",
                )
            else:
                db.session.add(MajorationSalle(
                    libelle=libelle, condition=_texte("condition", "soiree"),
                    seuil_minute=_heure_en_minutes("seuil_heure"),
                    pourcentage=pourcentage, montant_fixe=montant_fixe,
                ))
                db.session.commit()
                flash(f"Majoration « {libelle} » ajoutée.", "success")
        return redirect(url_for("salles.prestations"))

    return render_template(
        "salles/prestations.html",
        prestations=PrestationSalle.query.order_by(PrestationSalle.ordre, PrestationSalle.libelle).all(),
        majorations=MajorationSalle.query.order_by(MajorationSalle.condition).all(),
        unites=UNITES_PRESTATION, unites_labels=UNITES_PRESTATION_LABELS,
        conditions=CONDITIONS_MAJORATION, conditions_labels=CONDITIONS_MAJORATION_LABELS,
    )


def _heure_en_minutes(champ: str):
    from app.models import minutes_depuis_texte

    return minutes_depuis_texte(_texte(champ))


@bp.route("/prestations/<int:prestation_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:tarifs")
def prestation_supprimer(prestation_id: int):
    prestation = PrestationSalle.query.get_or_404(prestation_id)
    db.session.delete(prestation)
    db.session.commit()
    flash("Prestation supprimée. Les réservations qui l'utilisaient gardent leur prix.", "success")
    return redirect(url_for("salles.prestations"))


@bp.route("/majorations/<int:majoration_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:tarifs")
def majoration_supprimer(majoration_id: int):
    majoration = MajorationSalle.query.get_or_404(majoration_id)
    db.session.delete(majoration)
    db.session.commit()
    flash("Majoration supprimée.", "success")
    return redirect(url_for("salles.prestations"))


# ---------------------------------------------------------------------------
# Preneurs
# ---------------------------------------------------------------------------

@bp.route("/preneurs")
@login_required
@require_perm("locations:view")
def preneurs():
    recherche = (request.args.get("q") or "").strip()
    q = Preneur.query
    if recherche:
        motif = f"%{recherche}%"
        q = q.filter(db.or_(Preneur.nom.ilike(motif), Preneur.contact_nom.ilike(motif)))
    return render_template(
        "salles/preneurs.html",
        preneurs=q.order_by(Preneur.actif.desc(), Preneur.nom).limit(200).all(),
        recherche=recherche, aujourdhui=date.today(),
    )


@bp.route("/preneur/nouveau", methods=["GET", "POST"])
@bp.route("/preneur/<int:preneur_id>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("locations:edit")
def preneur_form(preneur_id: int | None = None):
    preneur = Preneur.query.get_or_404(preneur_id) if preneur_id else None
    categories = CategoriePreneur.query.filter_by(actif=True).order_by(
        CategoriePreneur.ordre, CategoriePreneur.libelle
    ).all()

    if request.method == "POST":
        nom = _texte("nom")
        if not nom:
            flash("Le nom du preneur est obligatoire.", "danger")
        else:
            if preneur is None:
                preneur = Preneur(created_by_user_id=getattr(current_user, "id", None))
                db.session.add(preneur)
            preneur.nom = nom
            preneur.categorie_id = _entier("categorie_id")
            preneur.contact_nom = _texte("contact_nom") or None
            preneur.representant = _texte("representant") or None
            preneur.email = _texte("email") or None
            preneur.telephone = _texte("telephone") or None
            preneur.adresse = _texte("adresse") or None
            preneur.code_postal = _texte("code_postal") or None
            preneur.ville = _texte("ville") or None
            preneur.siret = _texte("siret") or None
            preneur.assurance_rc_fin = _date("assurance_rc_fin")
            preneur.assurance_reference = _texte("assurance_reference") or None
            preneur.notes = _texte("notes") or None
            preneur.actif = _case("actif")
            db.session.commit()
            journaliser("salles.preneur", cible=preneur.nom)
            flash(f"Preneur « {preneur.nom} » enregistré.", "success")
            return redirect(url_for("salles.preneurs"))

    return render_template(
        "salles/preneur_form.html", preneur=preneur, categories=categories,
        aujourdhui=date.today(),
    )


# ---------------------------------------------------------------------------
# Réservations
# ---------------------------------------------------------------------------

@bp.route("/reservations")
@login_required
@require_perm("locations:view")
def reservations():
    """La liste de l'accueil. La purge des options passées se fait ici :
    ouvrir cet écran suffit à nettoyer les fantômes, sans tâche planifiée."""
    liberees = purger_options_expirees()
    if liberees:
        flash(f"{liberees} option(s) arrivée(s) à échéance ont été libérées.", "info")

    statut = (request.args.get("statut") or "").strip()
    q = Reservation.query
    if statut in STATUTS_RESERVATION:
        q = q.filter(Reservation.statut == statut)
    lignes = q.order_by(Reservation.created_at.desc()).limit(200).all()

    return render_template(
        "salles/reservations.html",
        reservations=lignes, statut=statut,
        statuts=STATUTS_RESERVATION, statuts_labels=STATUTS_RESERVATION_LABELS,
        aujourdhui=date.today(),
    )


@bp.route("/reservation/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("locations:edit")
def reservation_nouvelle():
    """Une seule page : qui, quelle salle, quelles dates, quelles options.

    Le prix se calcule tout seul et s'affiche ligne par ligne à la
    validation — la secrétaire n'a aucun barème à connaître.
    """
    salles = espaces_louables()
    liste_preneurs = Preneur.query.filter_by(actif=True).order_by(Preneur.nom).all()
    catalogue = PrestationSalle.query.filter_by(actif=True).order_by(
        PrestationSalle.ordre, PrestationSalle.libelle
    ).all()

    if request.method == "POST":
        preneur = Preneur.query.get(_entier("preneur_id") or 0)
        espace = Espace.query.get(_entier("espace_id") or 0)
        titre = _texte("titre")
        debut = _texte("heure_debut", "09:00")
        fin = _texte("heure_fin", "12:00")
        date_debut = _date("date_debut")
        date_fin = _date("date_fin") or date_debut

        erreurs = []
        if preneur is None:
            erreurs.append("Choisis un preneur.")
        if espace is None:
            erreurs.append("Choisis une salle.")
        if not titre:
            erreurs.append("L'intitulé est obligatoire.")
        if not date_debut:
            erreurs.append("Il faut au moins une date.")
        try:
            normaliser_plage(debut, fin)
        except SalleErreur as exc:
            erreurs.append(str(exc))

        if erreurs:
            for message in erreurs:
                flash(message, "danger")
            return render_template(
                "salles/reservation_form.html", salles=salles, preneurs=liste_preneurs,
                catalogue=catalogue, aujourdhui=date.today(), reservation=None,
            )

        jours = {int(j) for j in request.form.getlist("jour_semaine") if str(j).isdigit()}
        dates = dates_recurrentes(
            date_debut, date_fin, jours or None,
            sauter_feries=_case("sauter_feries"),
        )
        if not dates:
            flash("Aucune date ne correspond à ces critères.", "danger")
            return render_template(
                "salles/reservation_form.html", salles=salles, preneurs=liste_preneurs,
                catalogue=catalogue, aujourdhui=date.today(), reservation=None,
            )

        reservation = Reservation(
            reference=reference_unique(date_debut),
            preneur_id=preneur.id, espace_id=espace.id,
            categorie_id=preneur.categorie_id,
            titre=titre, effectif=_entier("effectif"),
            statut="option" if _case("poser_option") else "confirmee",
            option_expire_le=_date("option_expire_le"),
            gratuite=_case("gratuite"),
            motif_gratuite=_texte("motif_gratuite") or None,
            caution_montant=_decimal("caution_montant"),
            acompte_montant=_decimal("acompte_montant"),
            referent=_texte("referent") or None,
            conditions_particulieres=_texte("conditions_particulieres") or None,
            notes=_texte("notes") or None,
            created_by_user_id=getattr(current_user, "id", None),
        )
        # Une catégorie gratuite par principe : on pré-coche, le motif reste dû.
        if preneur.categorie is not None and preneur.categorie.gratuit_par_defaut:
            reservation.gratuite = True
            if not reservation.motif_gratuite:
                reservation.motif_gratuite = f"Catégorie « {preneur.categorie.libelle} » : gratuité de principe."
        db.session.add(reservation)
        db.session.flush()

        for prestation in catalogue:
            quantite = _decimal(f"prestation_{prestation.id}")
            if not quantite or quantite <= 0:
                continue
            db.session.add(ReservationPrestation(
                reservation=reservation, prestation_id=prestation.id,
                libelle=prestation.libelle, montant_unitaire=prestation.montant,
                unite=prestation.unite, quantite=quantite,
            ))

        appliquer_dates(reservation, dates, debut, fin)
        db.session.flush()
        recalculer(reservation)
        db.session.commit()

        journaliser("salles.reservation", cible=reservation.reference,
                    details={"preneur": preneur.nom, "dates": len(dates)})
        flash(
            f"Réservation {reservation.reference} créée : {len(dates)} date(s), "
            f"{reservation.montant_du:.2f} €.",
            "success",
        )
        return redirect(url_for("salles.reservation_fiche", reservation_id=reservation.id))

    return render_template(
        "salles/reservation_form.html", salles=salles, preneurs=liste_preneurs,
        catalogue=catalogue, aujourdhui=date.today(), reservation=None,
        espace_prefere=request.args.get("espace_id", type=int),
        jour_prefere=(request.args.get("jour") or date.today().isoformat()),
    )


@bp.route("/reservation/<int:reservation_id>")
@login_required
@require_perm("locations:view")
def reservation_fiche(reservation_id: int):
    reservation = Reservation.query.get_or_404(reservation_id)
    return render_template(
        "salles/reservation_fiche.html",
        r=reservation, controles=controles(reservation),
        statuts=STATUTS_RESERVATION, statuts_labels=STATUTS_RESERVATION_LABELS,
        aujourdhui=date.today(),
    )


@bp.route("/reservation/<int:reservation_id>/statut", methods=["POST"])
@login_required
@require_perm("locations:edit")
def reservation_statut(reservation_id: int):
    """Confirmer, annuler, clore. La confirmation refuse de passer tant
    qu'un contrôle bloquant n'est pas levé — assurance périmée, conflit de
    dates, gratuité sans motif."""
    reservation = Reservation.query.get_or_404(reservation_id)
    vers = _texte("statut")
    if vers not in STATUTS_RESERVATION:
        flash("Statut inconnu.", "danger")
        return redirect(url_for("salles.reservation_fiche", reservation_id=reservation.id))

    if vers in ("confirmee", "realisee"):
        empechements = bloquants(reservation)
        if empechements:
            flash(
                "Impossible de confirmer : " + empechements[0]["message"]
                + (f" (et {len(empechements) - 1} autre(s) point(s))" if len(empechements) > 1 else ""),
                "danger",
            )
            return redirect(url_for("salles.reservation_fiche", reservation_id=reservation.id))

    ancien = reservation.statut
    reservation.statut = vers
    if vers != "option":
        reservation.option_expire_le = None
    synchroniser_statut_occupations(reservation)
    recalculer(reservation)
    db.session.commit()
    journaliser("salles.reservation_statut", cible=reservation.reference,
                details={"de": ancien, "vers": vers})
    flash(f"{reservation.reference} : {STATUTS_RESERVATION_LABELS[vers].lower()}.", "success")
    return redirect(url_for("salles.reservation_fiche", reservation_id=reservation.id))


@bp.route("/reservation/<int:reservation_id>/reglements", methods=["POST"])
@login_required
@require_perm("locations:edit")
def reservation_reglements(reservation_id: int):
    """Caution, acompte, solde, clés : le suivi du quotidien."""
    reservation = Reservation.query.get_or_404(reservation_id)
    reservation.caution_montant = _decimal("caution_montant")
    reservation.caution_encaissee = _case("caution_encaissee")
    reservation.caution_restituee_le = _date("caution_restituee_le")
    reservation.acompte_montant = _decimal("acompte_montant")
    reservation.acompte_regle_le = _date("acompte_regle_le")
    reservation.solde_regle_le = _date("solde_regle_le")
    reservation.cles_remises_le = _date("cles_remises_le")
    reservation.cles_rendues_le = _date("cles_rendues_le")

    # Prix imposé à la main : possible, mais jamais muet.
    montant_manuel = _decimal("montant_manuel")
    motif = _texte("motif_montant_manuel") or None
    if montant_manuel is not None and not motif:
        flash("Un prix saisi à la main doit être motivé : la raison de l'écart sera imprimée.", "danger")
    else:
        reservation.montant_manuel = montant_manuel
        reservation.motif_montant_manuel = motif

    db.session.commit()
    journaliser("salles.reservation_reglements", cible=reservation.reference)
    flash("Suivi mis à jour.", "success")
    return redirect(url_for("salles.reservation_fiche", reservation_id=reservation.id))


@bp.route("/reservation/<int:reservation_id>/supprimer", methods=["POST"])
@login_required
@require_perm("locations:edit")
def reservation_supprimer(reservation_id: int):
    reservation = Reservation.query.get_or_404(reservation_id)
    reference = reservation.reference
    db.session.delete(reservation)
    if commit_delete("cette réservation", f"Réservation {reference} supprimée, dates comprises."):
        journaliser("salles.reservation_suppression", cible=reference)
    return redirect(url_for("salles.reservations"))
