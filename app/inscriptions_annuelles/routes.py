"""Module Inscriptions annuelles — saisie, suivi, règlement, fiche, export.

Portée secteur : sans ``scope:all_secteurs``, on ne voit que les bulletins
de son propre secteur — ceux qu'on a saisis (``created_secteur``) et ceux
dont on est le secteur qui fait venir (``secteur_orienteur``). Les bulletins
sans secteur restent visibles de tous : à l'accueil, on ne sait pas toujours
qui envoie la personne, et un bulletin invisible est un bulletin perdu.
"""
from __future__ import annotations

from datetime import date, datetime

from flask import abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    DEMI_JOURNEES,
    DEMI_JOURNEES_LABELS,
    JOURS_SEMAINE,
    JOURS_SEMAINE_LABELS,
    MODES_PAIEMENT,
    MODES_PAIEMENT_LABELS,
    STATUTS_INSCRIPTION_ANNUELLE,
    STATUTS_INSCRIPTION_ANNUELLE_LABELS,
    InscriptionAnnuelle,
    Participant,
    Quartier,
)
from app.rbac import can, require_perm
from app.secteurs import get_secteur_labels
from app.services.audit import journaliser
from app.services.cotisations import annee_scolaire_courante, libelle_annee_scolaire
from app.services.inscriptions_annuelles import (
    InscriptionAnnuelleErreur,
    annees_disponibles,
    annuler_reglement,
    appliquer_ateliers,
    appliquer_disponibilites,
    ateliers_proposables,
    confirmer_reglement,
    creer_participant,
    doublons_possibles,
    export_xlsx,
    montant_adhesion_suggere,
    rafraichir_statuts,
    rattacher_participant,
    synthese,
)

from . import bp


# ---------------------------------------------------------------------------
# Helpers de portée et de saisie
# ---------------------------------------------------------------------------

def _secteur_utilisateur() -> str:
    return (getattr(current_user, "secteur_assigne", "") or "").strip()


def _portee_globale() -> bool:
    return can("scope:all_secteurs")


def _restreindre_a_mon_secteur(query):
    """Applique le cloisonnement par secteur (sans effet en portée globale)."""
    if _portee_globale():
        return query
    secteur = _secteur_utilisateur()
    if not secteur:
        return query
    return query.filter(
        db.or_(
            InscriptionAnnuelle.created_secteur == secteur,
            InscriptionAnnuelle.secteur_orienteur == secteur,
            db.and_(
                InscriptionAnnuelle.created_secteur.is_(None),
                InscriptionAnnuelle.secteur_orienteur.is_(None),
            ),
        )
    )


def _accessible(inscription: InscriptionAnnuelle) -> bool:
    if _portee_globale():
        return True
    secteur = _secteur_utilisateur()
    if not secteur:
        return True
    if not inscription.created_secteur and not inscription.secteur_orienteur:
        return True
    return secteur in {inscription.created_secteur, inscription.secteur_orienteur}


def _charger(inscription_id: int) -> InscriptionAnnuelle:
    inscription = db.get_or_404(InscriptionAnnuelle, inscription_id)
    if not _accessible(inscription):
        abort(403)
    return inscription


def _annee_demandee() -> int:
    brut = (request.args.get("annee") or request.form.get("annee") or "").strip()
    try:
        return max(2000, min(2100, int(brut)))
    except (TypeError, ValueError):
        return annee_scolaire_courante()


def _date_form(nom: str, defaut: date | None = None) -> date | None:
    brut = (request.form.get(nom) or "").strip()
    if not brut:
        return defaut
    try:
        return datetime.strptime(brut, "%Y-%m-%d").date()
    except ValueError:
        return defaut


def _montant_form(nom: str) -> float | None:
    brut = (request.form.get(nom) or "").strip().replace(",", ".")
    if not brut:
        return None
    try:
        return round(float(brut), 2)
    except ValueError:
        return None


def _texte(nom: str, maxi: int | None = None) -> str | None:
    valeur = (request.form.get(nom) or "").strip()
    if not valeur:
        return None
    return valeur[:maxi] if maxi else valeur


def _email_invalide(email: str | None) -> bool:
    if not email:
        return False
    return "@" not in email or "." not in email.rsplit("@", 1)[-1]


def _appliquer_formulaire(inscription: InscriptionAnnuelle) -> list[str]:
    """Recopie le formulaire sur le bulletin. Retourne les erreurs bloquantes."""
    erreurs: list[str] = []

    inscription.nom = (request.form.get("nom") or "").strip()
    inscription.prenom = (request.form.get("prenom") or "").strip()
    if not inscription.nom or not inscription.prenom:
        erreurs.append("Le nom et le prénom sont obligatoires.")

    email = _texte("email", 180)
    if _email_invalide(email):
        erreurs.append("Adresse e-mail invalide (ex. nom@domaine.fr).")
    inscription.email = email

    inscription.adresse = _texte("adresse", 255)
    inscription.code_postal = _texte("code_postal", 10)
    inscription.ville = _texte("ville", 120)
    inscription.telephone = _texte("telephone", 60)
    inscription.genre = _texte("genre", 20)
    inscription.date_naissance = _date_form("date_naissance", None)
    inscription.secteur_orienteur = _texte("secteur_orienteur", 80)
    inscription.ateliers_libre = _texte("ateliers_libre")
    inscription.commentaire = _texte("commentaire")
    inscription.date_inscription = _date_form("date_inscription", inscription.date_inscription or date.today())

    inscription.benevolat_souhaite = (request.form.get("benevolat_souhaite") or "") == "1"
    if inscription.benevolat_souhaite:
        inscription.benevolat_mission = _texte("benevolat_mission")
        inscription.benevolat_dispo_inconnue = (request.form.get("benevolat_dispo_inconnue") or "") == "1"
        appliquer_disponibilites(inscription, request.form.getlist("creneaux"))
    else:
        # L'envie retirée, on ne garde pas des créneaux orphelins qui
        # feraient remonter la personne dans la grille des bénévoles.
        inscription.benevolat_mission = None
        inscription.benevolat_dispo_inconnue = False
        appliquer_disponibilites(inscription, [])

    appliquer_ateliers(inscription, request.form.getlist("ateliers"))
    return erreurs


def _contexte_formulaire(inscription: InscriptionAnnuelle | None, annee: int) -> dict:
    secteur = None if _portee_globale() else (_secteur_utilisateur() or None)
    return {
        "inscription": inscription,
        "pending": None,
        "annee": annee,
        "libelle_annee": libelle_annee_scolaire(annee),
        "annees": annees_disponibles(),
        "secteurs": get_secteur_labels(active_only=True),
        "ateliers": ateliers_proposables(secteur),
        "jours": JOURS_SEMAINE,
        "jours_labels": JOURS_SEMAINE_LABELS,
        "demi_journees": DEMI_JOURNEES,
        "demi_journees_labels": DEMI_JOURNEES_LABELS,
        "secteur_defaut": _secteur_utilisateur(),
    }


# ---------------------------------------------------------------------------
# Liste / tableau de bord de la campagne
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
@require_perm("inscriptions_annuelles:view")
def index():
    annee = _annee_demandee()

    # Filet de sécurité : recale les bulletins dont la personne est déjà
    # venue (reprise de données, import massif) avant d'afficher les compteurs.
    try:
        rafraichir_statuts(annee)
    except Exception:  # noqa: BLE001 — l'affichage ne dépend pas du rattrapage
        db.session.rollback()

    q = _restreindre_a_mon_secteur(
        InscriptionAnnuelle.query.filter(InscriptionAnnuelle.annee_scolaire == annee)
    )

    statut = (request.args.get("statut") or "").strip()
    if statut in STATUTS_INSCRIPTION_ANNUELLE:
        q = q.filter(InscriptionAnnuelle.statut == statut)

    secteur = (request.args.get("secteur") or "").strip()
    if secteur:
        q = q.filter(InscriptionAnnuelle.secteur_orienteur == secteur)

    reglement = (request.args.get("reglement") or "").strip()
    if reglement == "regle":
        q = q.filter(InscriptionAnnuelle.reglement_confirme.is_(True))
    elif reglement == "a_regler":
        q = q.filter(InscriptionAnnuelle.reglement_confirme.is_(False))

    if (request.args.get("benevolat") or "") == "1":
        q = q.filter(InscriptionAnnuelle.benevolat_souhaite.is_(True))

    recherche = (request.args.get("q") or "").strip()
    if recherche:
        motif = f"%{recherche}%"
        q = q.filter(db.or_(
            InscriptionAnnuelle.nom.ilike(motif),
            InscriptionAnnuelle.prenom.ilike(motif),
            InscriptionAnnuelle.email.ilike(motif),
            InscriptionAnnuelle.telephone.ilike(motif),
        ))

    inscriptions = q.order_by(
        InscriptionAnnuelle.nom.asc(), InscriptionAnnuelle.prenom.asc(), InscriptionAnnuelle.id.asc()
    ).all()

    # Les compteurs portent sur la campagne entière (périmètre visible),
    # pas sur le filtre en cours : on veut savoir où en est la rentrée.
    toutes = _restreindre_a_mon_secteur(
        InscriptionAnnuelle.query.filter(InscriptionAnnuelle.annee_scolaire == annee)
    ).all()

    return render_template(
        "inscriptions_annuelles/index.html",
        annee=annee,
        libelle_annee=libelle_annee_scolaire(annee),
        annees=annees_disponibles(),
        inscriptions=inscriptions,
        data=synthese(annee, toutes),
        secteurs=get_secteur_labels(active_only=True),
        statuts=STATUTS_INSCRIPTION_ANNUELLE,
        statuts_labels=STATUTS_INSCRIPTION_ANNUELLE_LABELS,
        filtres={
            "statut": statut, "secteur": secteur, "reglement": reglement,
            "q": recherche, "benevolat": (request.args.get("benevolat") or ""),
        },
        peut_editer=can("inscriptions_annuelles:edit"),
        peut_regler=can("inscriptions_annuelles:reglement"),
        peut_exporter=can("inscriptions_annuelles:export"),
    )


# ---------------------------------------------------------------------------
# Saisie et modification d'un bulletin
# ---------------------------------------------------------------------------

@bp.route("/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("inscriptions_annuelles:edit")
def nouvelle():
    annee = _annee_demandee()

    if request.method == "POST":
        inscription = InscriptionAnnuelle(
            annee_scolaire=annee,
            date_inscription=date.today(),
            created_secteur=_secteur_utilisateur() or None,
            created_by_user_id=getattr(current_user, "id", None),
        )
        # Ajout AVANT la recopie du formulaire : cocher un atelier rattache le
        # bulletin à un objet déjà suivi par la session, et un objet encore
        # détaché déclencherait un avertissement SQLAlchemy au premier autoflush.
        db.session.add(inscription)

        erreurs = _appliquer_formulaire(inscription)
        if erreurs:
            db.session.rollback()
            for message in erreurs:
                flash(message, "err")
            contexte = _contexte_formulaire(None, annee)
            contexte["pending"] = request.form
            return render_template("inscriptions_annuelles/form.html", **contexte)

        db.session.commit()
        journaliser("inscription_annuelle.create", cible=f"{inscription.nom_complet} ({inscription.libelle_annee})")
        flash(f"Inscription de {inscription.nom_complet} enregistrée.", "ok")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    return render_template("inscriptions_annuelles/form.html", **_contexte_formulaire(None, annee))


@bp.route("/<int:inscription_id>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("inscriptions_annuelles:edit")
def modifier(inscription_id: int):
    inscription = _charger(inscription_id)

    if request.method == "POST":
        erreurs = _appliquer_formulaire(inscription)
        if erreurs:
            # On annule la saisie invalide en base ; le formulaire se réaffiche
            # depuis ``pending``, donc la personne ne perd pas ce qu'elle a tapé.
            db.session.rollback()
            for message in erreurs:
                flash(message, "err")
            contexte = _contexte_formulaire(inscription, inscription.annee_scolaire)
            contexte["pending"] = request.form
            return render_template("inscriptions_annuelles/form.html", **contexte)

        db.session.commit()
        flash("Inscription mise à jour.", "ok")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    return render_template(
        "inscriptions_annuelles/form.html",
        **_contexte_formulaire(inscription, inscription.annee_scolaire),
    )


@bp.route("/<int:inscription_id>")
@login_required
@require_perm("inscriptions_annuelles:view")
def detail(inscription_id: int):
    inscription = _charger(inscription_id)

    doublons = []
    if can("inscriptions_annuelles:edit") and not inscription.participant_id:
        doublons = doublons_possibles(inscription)

    quartiers = []
    if can("inscriptions_annuelles:edit"):
        quartiers = Quartier.query.order_by(Quartier.ville.asc(), Quartier.nom.asc()).all()

    return render_template(
        "inscriptions_annuelles/detail.html",
        inscription=inscription,
        doublons=doublons,
        quartiers=quartiers,
        modes_paiement=MODES_PAIEMENT,
        modes_paiement_labels=MODES_PAIEMENT_LABELS,
        montant_suggere=montant_adhesion_suggere(inscription.annee_scolaire),
        aujourdhui=date.today(),
        peut_editer=can("inscriptions_annuelles:edit"),
        peut_regler=can("inscriptions_annuelles:reglement"),
    )


@bp.route("/<int:inscription_id>/statut", methods=["POST"])
@login_required
@require_perm("inscriptions_annuelles:edit")
def changer_statut(inscription_id: int):
    """Annulation (désistement) et réactivation d'un bulletin.

    On n'efface jamais un bulletin annulé : il compte dans le bilan de la
    campagne (« combien de désistements ? ») et garde la trace du travail
    d'accueil déjà fait."""
    inscription = _charger(inscription_id)
    action = (request.form.get("action") or "").strip()

    if action == "annuler":
        inscription.statut = "annulee"
        flash(f"Inscription de {inscription.nom_complet} annulée.", "ok")
    elif action == "reactiver":
        if inscription.participant_id:
            inscription.statut = "active" if inscription.premiere_participation_le else "en_attente"
        else:
            inscription.statut = "saisie"
        flash(f"Inscription de {inscription.nom_complet} réactivée.", "ok")
    else:
        abort(400)

    db.session.commit()
    return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))


@bp.route("/<int:inscription_id>/supprimer", methods=["POST"])
@login_required
@require_perm("inscriptions_annuelles:edit")
def supprimer(inscription_id: int):
    """Suppression d'un bulletin saisi par erreur (doublon de saisie, test).

    Refusée dès qu'une fiche participant en dépend : dans ce cas, on annule
    (le bulletin garde sa trace) au lieu de faire disparaître l'historique."""
    inscription = _charger(inscription_id)
    if inscription.participant_id:
        flash(
            "Ce bulletin est rattaché à une fiche participant : annule-le plutôt "
            "que de le supprimer, pour garder la trace de l'inscription.",
            "err",
        )
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    annee, nom = inscription.annee_scolaire, inscription.nom_complet
    db.session.delete(inscription)
    db.session.commit()
    journaliser("inscription_annuelle.delete", cible=f"{nom} ({libelle_annee_scolaire(annee)})")
    flash(f"Inscription de {nom} supprimée.", "ok")
    return redirect(url_for("inscriptions_annuelles.index", annee=annee))


# ---------------------------------------------------------------------------
# Transformation en fiche participant
# ---------------------------------------------------------------------------

@bp.route("/<int:inscription_id>/creer-participant", methods=["POST"])
@login_required
@require_perm("inscriptions_annuelles:edit")
def creer_fiche(inscription_id: int):
    inscription = _charger(inscription_id)

    quartier_id = None
    try:
        quartier_id = int(request.form.get("quartier_id") or 0) or None
    except (TypeError, ValueError):
        quartier_id = None

    try:
        participant, avertissements = creer_participant(
            inscription,
            user_id=getattr(current_user, "id", None),
            secteur=(inscription.secteur_orienteur or _secteur_utilisateur() or None),
            quartier_id=quartier_id,
            marquer_benevole=(request.form.get("marquer_benevole") or "") == "1",
            inscrire_ateliers=(request.form.get("inscrire_ateliers") or "1") == "1",
        )
    except InscriptionAnnuelleErreur as exc:
        flash(str(exc), "err")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    journaliser("inscription_annuelle.participant", cible=f"{participant.nom} {participant.prenom}")
    flash(
        f"Fiche participant créée pour {inscription.nom_complet} — en attente de première participation.",
        "ok",
    )
    for message in avertissements:
        flash(message, "warn")
    return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))


@bp.route("/<int:inscription_id>/rattacher", methods=["POST"])
@login_required
@require_perm("inscriptions_annuelles:edit")
def rattacher_fiche(inscription_id: int):
    inscription = _charger(inscription_id)
    participant = db.session.get(Participant, request.form.get("participant_id", type=int) or 0)
    if participant is None:
        flash("Fiche participant introuvable.", "err")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    try:
        avertissements = rattacher_participant(
            inscription, participant,
            user_id=getattr(current_user, "id", None),
            inscrire_ateliers=(request.form.get("inscrire_ateliers") or "1") == "1",
        )
    except InscriptionAnnuelleErreur as exc:
        flash(str(exc), "err")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    flash(f"Inscription rattachée à la fiche de {participant.prenom} {participant.nom}.", "ok")
    for message in avertissements:
        flash(message, "warn")
    return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))


# ---------------------------------------------------------------------------
# Règlement
# ---------------------------------------------------------------------------

@bp.route("/<int:inscription_id>/reglement", methods=["POST"])
@login_required
@require_perm("inscriptions_annuelles:reglement")
def reglement(inscription_id: int):
    inscription = _charger(inscription_id)
    action = (request.form.get("action") or "confirmer").strip()

    if action == "annuler":
        annuler_reglement(inscription)
        flash("Règlement repassé en attente.", "ok")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    mode = (request.form.get("mode") or "").strip()
    if mode and mode not in MODES_PAIEMENT:
        mode = None

    info = confirmer_reglement(
        inscription,
        montant=_montant_form("montant"),
        mode=mode,
        date_reglement=_date_form("date_reglement", date.today()),
        commentaire=_texte("reglement_commentaire", 255),
        creer_adhesion=(request.form.get("creer_adhesion") or "") == "1",
        user_id=getattr(current_user, "id", None),
    )
    flash(f"Règlement confirmé pour {inscription.nom_complet}.", "ok")
    if info:
        flash(info, "ok" if "enregistré" in info else "warn")
    return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))


# ---------------------------------------------------------------------------
# Fiche imprimable (à donner à l'accueil au moment du paiement)
# ---------------------------------------------------------------------------

@bp.route("/<int:inscription_id>/fiche")
@login_required
@require_perm("inscriptions_annuelles:view")
def fiche(inscription_id: int):
    inscription = _charger(inscription_id)
    return render_template(
        "inscriptions_annuelles/fiche.html",
        inscription=inscription,
        modes_paiement_labels=MODES_PAIEMENT_LABELS,
        montant_suggere=montant_adhesion_suggere(inscription.annee_scolaire),
        jours=JOURS_SEMAINE,
        jours_labels=JOURS_SEMAINE_LABELS,
        demi_journees=DEMI_JOURNEES,
        demi_journees_labels=DEMI_JOURNEES_LABELS,
    )


# ---------------------------------------------------------------------------
# Export XLSX
# ---------------------------------------------------------------------------

@bp.route("/export.xlsx")
@login_required
@require_perm("inscriptions_annuelles:export")
def export():
    annee = _annee_demandee()
    inscriptions = _restreindre_a_mon_secteur(
        InscriptionAnnuelle.query.filter(InscriptionAnnuelle.annee_scolaire == annee)
    ).order_by(
        InscriptionAnnuelle.nom.asc(), InscriptionAnnuelle.prenom.asc(), InscriptionAnnuelle.id.asc()
    ).all()

    classeur = export_xlsx(annee, inscriptions)
    journaliser("inscription_annuelle.export", cible=libelle_annee_scolaire(annee),
                details={"lignes": len(inscriptions)})
    return send_file(
        classeur,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"inscriptions_{libelle_annee_scolaire(annee)}.xlsx",
    )
