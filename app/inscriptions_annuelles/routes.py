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
    TYPES_INSCRIPTION_ANNUELLE,
    TYPES_INSCRIPTION_ANNUELLE_LABELS,
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
    appliquer_membres,
    ateliers_proposables,
    calculer_cout,
    creer_participant,
    doublons_possibles,
    encaisser,
    etat_reglement,
    export_xlsx,
    generer_cotisations,
    personnes_couvertes,
    rafraichir_reglements,
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


def _lignes_membres() -> list[dict]:
    """Lit les lignes « membre du foyer » du formulaire.

    Les champs arrivent en tableaux parallèles (``membre_prenom[]``…), un
    index par ligne : la famille en ajoute autant qu'elle veut, le navigateur
    les envoie dans l'ordre. Une ligne sans prénom est une ligne vide, elle
    sera ignorée en aval."""
    ids = request.form.getlist("membre_id")
    prenoms = request.form.getlist("membre_prenom")
    noms = request.form.getlist("membre_nom")
    naissances = request.form.getlist("membre_date_naissance")
    liens = request.form.getlist("membre_lien")

    lignes = []
    for i, prenom in enumerate(prenoms):
        brut_date = (naissances[i] if i < len(naissances) else "").strip()
        try:
            naissance = datetime.strptime(brut_date, "%Y-%m-%d").date() if brut_date else None
        except ValueError:
            naissance = None
        lignes.append({
            "id": ids[i] if i < len(ids) else None,
            "prenom": prenom,
            "nom": noms[i] if i < len(noms) else "",
            "date_naissance": naissance,
            "lien_filiation": liens[i] if i < len(liens) else "",
        })
    return lignes


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

    type_inscription = (request.form.get("type_inscription") or "individuelle").strip()
    inscription.type_inscription = (
        type_inscription if type_inscription in TYPES_INSCRIPTION_ANNUELLE else "individuelle"
    )
    if inscription.est_familiale:
        appliquer_membres(inscription, _lignes_membres())
    else:
        # Repasser en individuelle vide la composition du foyer — sauf les
        # membres qui ont déjà une fiche : on ne fait pas disparaître une
        # personne de l'application d'un coup de case à cocher.
        appliquer_membres(inscription, [])

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


def _tarifs_annee(annee: int, a_la_date: date | None = None) -> dict:
    """Les trois tarifs en vigueur, en euros ou None si absents du barème."""
    from app.services.cotisations import tarif_en_vigueur

    tarifs = {}
    for code in ("adhesion_individuelle", "adhesion_familiale", "participation"):
        ligne = tarif_en_vigueur(annee, code, a_la_date or date.today())
        tarifs[code] = round(float(ligne.montant), 2) if ligne else None
    return tarifs


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
        "types_inscription": TYPES_INSCRIPTION_ANNUELLE,
        "types_inscription_labels": TYPES_INSCRIPTION_ANNUELLE_LABELS,
        # Barème de l'année, pour l'estimation du coût pendant la saisie.
        # Le montant qui fait foi reste celui calculé au serveur.
        "tarifs_json": _tarifs_annee(annee),
    }


# ---------------------------------------------------------------------------
# Liste / tableau de bord de la campagne
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
@require_perm("inscriptions_annuelles:view")
def index():
    annee = _annee_demandee()

    # Filets de sécurité avant d'afficher les compteurs : les bulletins dont
    # la personne est déjà venue, et les règlements saisis ailleurs (une fiche
    # participant peut encaisser sans passer par ce module).
    try:
        rafraichir_statuts(annee)
        rafraichir_reglements(annee)
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
    if reglement in ("complet", "partiel", "rien"):
        q = q.filter(InscriptionAnnuelle.reglement_statut == reglement)
    elif reglement == "a_regler":
        q = q.filter(InscriptionAnnuelle.reglement_statut != "complet")

    type_inscription = (request.args.get("type_inscription") or "").strip()
    if type_inscription in TYPES_INSCRIPTION_ANNUELLE:
        q = q.filter(InscriptionAnnuelle.type_inscription == type_inscription)

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
        types_inscription=TYPES_INSCRIPTION_ANNUELLE,
        types_inscription_labels=TYPES_INSCRIPTION_ANNUELLE_LABELS,
        filtres={
            "statut": statut, "secteur": secteur, "reglement": reglement,
            "q": recherche, "benevolat": (request.args.get("benevolat") or ""),
            "type_inscription": type_inscription,
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
        cout=calculer_cout(inscription),
        etat=etat_reglement(inscription),
        personnes=personnes_couvertes(inscription),
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
    """Encaisse une somme — totale ou partielle — ou remet le compteur à zéro."""
    inscription = _charger(inscription_id)
    action = (request.form.get("action") or "encaisser").strip()

    if action == "annuler":
        try:
            annuler_reglement(inscription)
        except InscriptionAnnuelleErreur as exc:
            flash(str(exc), "err")
            return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))
        flash("Règlement remis à zéro sur le bulletin.", "ok")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    mode = (request.form.get("mode") or "").strip()
    if mode not in MODES_PAIEMENT:
        mode = "especes"

    montant = _montant_form("montant")
    if (request.form.get("solder") or "") == "1":
        # Bouton « solder » : encaisse exactement ce qu'il reste.
        montant = etat_reglement(inscription)["reste"]

    try:
        _, message = encaisser(
            inscription,
            montant or 0,
            mode=mode,
            date_paiement=_date_form("date_reglement", date.today()),
            commentaire=_texte("reglement_commentaire", 255),
            user_id=getattr(current_user, "id", None),
        )
    except InscriptionAnnuelleErreur as exc:
        flash(str(exc), "err")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    flash(message, "ok")
    return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))


@bp.route("/<int:inscription_id>/cotisations", methods=["POST"])
@login_required
@require_perm("inscriptions_annuelles:reglement")
def cotisations(inscription_id: int):
    """(Re)génère l'adhésion et les participations dans le module Adhésions.

    Utile quand un membre du foyer reçoit sa fiche après coup, ou quand le
    barème n'était pas encore saisi au moment de l'inscription."""
    inscription = _charger(inscription_id)
    try:
        creees, avertissements = generer_cotisations(
            inscription, user_id=getattr(current_user, "id", None)
        )
    except InscriptionAnnuelleErreur as exc:
        flash(str(exc), "err")
        return redirect(url_for("inscriptions_annuelles.detail", inscription_id=inscription.id))

    flash(
        f"{len(creees)} cotisation(s) créée(s) dans Adhésions & participation."
        if creees else "Les cotisations de cette inscription étaient déjà à jour.",
        "ok",
    )
    for message in avertissements:
        flash(message, "warn")
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
        cout=calculer_cout(inscription),
        etat=etat_reglement(inscription),
        personnes=personnes_couvertes(inscription),
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
