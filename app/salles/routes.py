"""Module Salles & espaces — référentiel des lieux, disponibilité, stockage.

Trois écrans seulement à ce stade, mais ce sont les fondations :

- le PLAN : l'arbre des espaces, qu'on construit et qu'on retouche ;
- la DISPONIBILITÉ : « qui est libre ce jour-là, à cette heure, pour tant
  de personnes ? » — l'écran de l'accueil ;
- la REPRISE INVENTAIRE : transformer les localisations tapées à la main
  en emplacements structurés, sans rien ressaisir.
"""
from __future__ import annotations

from datetime import date, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    ORIGINES_MANUELLES,
    ORIGINES_MANUELLES_AIDE,
    ORIGINES_OCCUPATION_LABELS,
    REGIMES_SOUS_LOCATION,
    REGIMES_SOUS_LOCATION_LABELS,
    TYPES_ESPACE,
    TYPES_ESPACE_LABELS,
    TYPES_SITE,
    TYPES_SITE_LABELS,
    Espace,
    InventaireItem,
    Occupation,
    Site,
)
from app.rbac import require_perm
from app.services.audit import journaliser
from app.services.salles import (
    COULEURS_ORIGINE,
    SalleErreur,
    arbre_du_site,
    conflits,
    espaces_reservables,
    espaces_stockage,
    normaliser_plage,
    planning_du_jour,
    planning_mois,
    planning_semaine,
    recherche_disponibilite,
    reconcilier_occupations,
    zone_conflit_ids,
)
from app.services.temps_ouverture import (
    HORAIRES_DEFAUT,
    JOURS_SEMAINE_ORDRE,
    MOIS_FR,
    ecrire_horaires,
    jour_ferie,
    jours_feries,
    libelle_jour,
    lire_horaires,
)
from app.services.salles_seed import installer_plan
from app.utils.delete_guard import commit_delete

from . import bp


# ---------------------------------------------------------------------------
# Helpers de saisie
# ---------------------------------------------------------------------------

def _texte(champ: str, defaut: str = "") -> str:
    return (request.form.get(champ) or defaut).strip()


def _case(champ: str) -> bool:
    return request.form.get(champ) in ("1", "on", "true", "oui")


def _entier(champ: str):
    brut = (request.form.get(champ) or "").strip()
    if not brut:
        return None
    try:
        return int(brut)
    except ValueError:
        return None


def _decimal(champ: str):
    brut = (request.form.get(champ) or "").strip().replace(",", ".")
    if not brut:
        return None
    try:
        return float(brut)
    except ValueError:
        return None


def _date(champ: str):
    brut = (request.form.get(champ) or "").strip()
    if not brut:
        return None
    try:
        return date.fromisoformat(brut)
    except ValueError:
        return None


def _date_arg(nom: str):
    """Lit une date passée en paramètre d'URL, en ignorant ce qui est illisible."""
    brut = (request.args.get(nom) or "").strip()
    if not brut:
        return None
    try:
        return date.fromisoformat(brut)
    except ValueError:
        return None


def _code_unique(nom: str) -> str:
    """Code court et stable dérivé du nom, suffixé si déjà pris."""
    import re
    import unicodedata

    base = unicodedata.normalize("NFKD", nom or "site")
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower()[:30] or "site"
    code, n = base, 1
    while Site.query.filter(Site.code == code).first():
        n += 1
        code = f"{base}-{n}"[:40]
    return code


# ---------------------------------------------------------------------------
# Le plan : l'arbre des espaces
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
@require_perm("salles:view")
def index():
    sites = Site.query.order_by(Site.actif.desc(), Site.nom).all()
    arbres = {site.id: arbre_du_site(site.id) for site in sites}
    localisations_a_reprendre = (
        InventaireItem.query
        .filter(InventaireItem.espace_id.is_(None))
        .filter(InventaireItem.localisation.isnot(None), InventaireItem.localisation != "")
        .count()
    )
    return render_template(
        "salles/index.html",
        sites=sites,
        arbres=arbres,
        localisations_a_reprendre=localisations_a_reprendre,
    )


@bp.route("/site/nouveau", methods=["GET", "POST"])
@bp.route("/site/<int:site_id>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("salles:edit")
def site_form(site_id: int | None = None):
    site = Site.query.get_or_404(site_id) if site_id else None

    if request.method == "POST":
        nom = _texte("nom")
        if not nom:
            flash("Le nom du site est obligatoire.", "danger")
            return render_template(
                "salles/site_form.html", site=site,
                types_site=TYPES_SITE, types_site_labels=TYPES_SITE_LABELS,
                regimes=REGIMES_SOUS_LOCATION, regimes_labels=REGIMES_SOUS_LOCATION_LABELS,
            )

        if site is None:
            site = Site(code=_code_unique(nom))
            db.session.add(site)

        site.nom = nom
        site.type_site = _texte("type_site", "propriete")
        site.adresse = _texte("adresse") or None
        site.code_postal = _texte("code_postal") or None
        site.ville = _texte("ville") or None
        site.proprietaire = _texte("proprietaire") or None
        site.convention_reference = _texte("convention_reference") or None
        site.convention_debut = _date("convention_debut")
        site.convention_fin = _date("convention_fin")
        site.regime_sous_location = _texte("regime_sous_location", "inconnue")
        site.convention_notes = _texte("convention_notes") or None
        site.valeur_locative_annuelle = _decimal("valeur_locative_annuelle")
        site.actif = _case("actif")

        db.session.commit()
        journaliser("salles.site", cible=site.code, details={"nom": site.nom})
        flash(f"Site « {site.nom} » enregistré.", "success")
        return redirect(url_for("salles.index"))

    return render_template(
        "salles/site_form.html", site=site,
        types_site=TYPES_SITE, types_site_labels=TYPES_SITE_LABELS,
        regimes=REGIMES_SOUS_LOCATION, regimes_labels=REGIMES_SOUS_LOCATION_LABELS,
    )


@bp.route("/site/<int:site_id>/installer-plan", methods=["POST"])
@login_required
@require_perm("salles:edit")
def site_installer_plan(site_id: int):
    """Remplit un site vide avec le plan type d'un centre social."""
    site = Site.query.get_or_404(site_id)
    crees = installer_plan(site)
    if crees:
        journaliser("salles.plan_type", cible=site.code, details={"espaces": crees})
        flash(
            f"{crees} espaces créés. À toi de corriger les capacités et de cocher "
            "ce qui est louable — rien n'est figé.",
            "success",
        )
    else:
        flash("Ce site contient déjà des espaces : le plan type n'a pas été réinstallé.", "warning")
    return redirect(url_for("salles.index"))


@bp.route("/site/<int:site_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:edit")
def site_supprimer(site_id: int):
    site = Site.query.get_or_404(site_id)
    nom = site.nom
    db.session.delete(site)
    if commit_delete("ce site", f"Site « {nom} » supprimé, avec tous ses espaces."):
        journaliser("salles.site_suppression", cible=nom)
    return redirect(url_for("salles.index"))


@bp.route("/espace/nouveau", methods=["GET", "POST"])
@bp.route("/espace/<int:espace_id>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("salles:edit")
def espace_form(espace_id: int | None = None):
    espace = Espace.query.get_or_404(espace_id) if espace_id else None
    site_id = espace.site_id if espace else (request.values.get("site_id", type=int))
    site = Site.query.get_or_404(site_id) if site_id else None
    if site is None:
        flash("Crée d'abord un site (le bâtiment) avant d'y ajouter des espaces.", "warning")
        return redirect(url_for("salles.index"))

    # Parents possibles : tous les espaces du site, sauf soi-même et sa
    # propre descendance — sinon on fabrique une boucle dans l'arbre.
    interdits = zone_conflit_ids(espace) - {a.id for a in espace.ancetres()} if espace else set()
    parents_possibles = [
        (e, p) for (e, p) in arbre_du_site(site.id)
        if not espace or (e.id != espace.id and e.id not in interdits)
    ]

    if request.method == "POST":
        nom = _texte("nom")
        if not nom:
            flash("Le nom de l'espace est obligatoire.", "danger")
        else:
            if espace is None:
                espace = Espace(site=site)
                db.session.add(espace)

            parent_id = _entier("parent_id")
            if parent_id and parent_id not in {e.id for (e, _) in parents_possibles}:
                flash("Ce parent créerait une boucle dans le plan : ignoré.", "warning")
                parent_id = espace.parent_id

            espace.nom = nom
            espace.parent_id = parent_id
            espace.type_espace = _texte("type_espace", "salle")
            espace.ordre = _entier("ordre") or 0
            espace.reservable = _case("reservable")
            espace.louable = _case("louable")
            espace.stockage = _case("stockage")
            espace.securise = _case("securise")
            espace.detenteur_cle = _texte("detenteur_cle") or None
            espace.capacite_reglementaire = _entier("capacite_reglementaire")
            espace.capacite_usage = _entier("capacite_usage")
            espace.surface_m2 = _decimal("surface_m2")
            espace.pmr = _case("pmr")
            espace.battement_minutes = max(0, _entier("battement_minutes") or 0)
            espace.habilitation_requise = _texte("habilitation_requise") or None
            espace.equipements = _texte("equipements") or None
            espace.notes = _texte("notes") or None
            espace.valeur_locative_annuelle = _decimal("valeur_locative_annuelle")
            espace.actif = _case("actif")

            # Un espace louable est forcément réservable : sinon il serait
            # mis à disposition sans jamais apparaître dans un planning.
            if espace.louable and not espace.reservable:
                espace.reservable = True
                flash(
                    f"« {espace.nom} » est louable : il a été rendu réservable "
                    "automatiquement, sans quoi il n'apparaîtrait dans aucun planning.",
                    "info",
                )

            db.session.commit()
            journaliser("salles.espace", cible=espace.nom, details={"site": site.code})
            flash(f"Espace « {espace.nom} » enregistré.", "success")
            return redirect(url_for("salles.index") + f"#site-{site.id}")

    return render_template(
        "salles/espace_form.html",
        espace=espace, site=site,
        parents_possibles=parents_possibles,
        parent_prefere=request.values.get("parent_id", type=int),
        types_espace=TYPES_ESPACE, types_espace_labels=TYPES_ESPACE_LABELS,
    )


@bp.route("/espace/<int:espace_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:edit")
def espace_supprimer(espace_id: int):
    espace = Espace.query.get_or_404(espace_id)
    nom, site_id = espace.nom, espace.site_id
    nb_enfants = len(espace.descendants())
    db.session.delete(espace)
    message = f"Espace « {nom} » supprimé."
    if nb_enfants:
        message += f" {nb_enfants} espace(s) qu'il contenait ont été supprimés avec lui."
    if commit_delete("cet espace", message):
        journaliser("salles.espace_suppression", cible=nom)
    return redirect(url_for("salles.index") + f"#site-{site_id}")


@bp.route("/espace/<int:espace_id>")
@login_required
@require_perm("salles:view")
def espace_fiche(espace_id: int):
    """Fiche d'un espace : ce qui s'y passe, et ce qui s'y range."""
    espace = Espace.query.get_or_404(espace_id)
    aujourdhui = date.today()

    occupations = (
        Occupation.query
        .filter(Occupation.espace_id.in_(zone_conflit_ids(espace)))
        .filter(Occupation.date_jour >= aujourdhui)
        .filter(Occupation.date_jour <= aujourdhui + timedelta(days=60))
        .order_by(Occupation.date_jour, Occupation.minute_debut)
        .limit(100)
        .all()
    )
    items = (
        InventaireItem.query
        .filter(InventaireItem.espace_id == espace_id)
        .order_by(InventaireItem.designation)
        .all()
    )
    return render_template(
        "salles/espace_fiche.html",
        espace=espace, occupations=occupations, items=items, aujourdhui=aujourdhui,
    )


# ---------------------------------------------------------------------------
# Disponibilité : l'écran de l'accueil
# ---------------------------------------------------------------------------

@bp.route("/disponibilite")
@login_required
@require_perm("salles:view")
def disponibilite():
    jour = None
    brut_jour = (request.args.get("jour") or "").strip()
    if brut_jour:
        try:
            jour = date.fromisoformat(brut_jour)
        except ValueError:
            flash("Date illisible : on repart sur aujourd'hui.", "warning")
    jour = jour or date.today()

    debut = (request.args.get("debut") or "09:00").strip()
    fin = (request.args.get("fin") or "12:00").strip()
    effectif = request.args.get("effectif", type=int)
    louables_seulement = request.args.get("louables") == "1"

    lignes, erreur = [], None
    try:
        lignes = recherche_disponibilite(
            jour, debut, fin, effectif=effectif, louables_seulement=louables_seulement
        )
    except SalleErreur as exc:
        erreur = str(exc)

    return render_template(
        "salles/disponibilite.html",
        lignes=lignes, erreur=erreur, jour=jour, debut=debut, fin=fin,
        effectif=effectif, louables_seulement=louables_seulement,
    )


# ---------------------------------------------------------------------------
# Reprise des localisations d'inventaire
# ---------------------------------------------------------------------------

@bp.route("/inventaire/reprise", methods=["GET", "POST"])
@login_required
@require_perm("salles:edit")
def inventaire_reprise():
    """Transforme les localisations tapées à la main en emplacements réels.

    On regroupe par texte distinct : « Salle info », « salle info. » et
    « SALLE INFO » se traitent d'un seul geste, au lieu d'ouvrir les
    fiches une par une.
    """
    emplacements = espaces_stockage()

    if request.method == "POST":
        # On ne fait confiance qu'aux emplacements réellement proposés :
        # un formulaire bricolé ne doit pas pouvoir ranger du matériel dans
        # un espace qui n'est pas déclaré point de stockage.
        autorises = {e.id for e in emplacements}
        reaffectes = 0
        for cle, valeur in request.form.items():
            if not cle.startswith("loc_") or not valeur:
                continue
            try:
                espace_id = int(valeur)
            except ValueError:
                continue
            if espace_id not in autorises:
                continue
            libelle = cle[4:]
            maj = (
                InventaireItem.query
                .filter(InventaireItem.espace_id.is_(None))
                .filter(db.func.lower(db.func.trim(InventaireItem.localisation)) == libelle)
                .update({"espace_id": espace_id}, synchronize_session=False)
            )
            reaffectes += maj or 0
        db.session.commit()
        if reaffectes:
            journaliser("salles.reprise_inventaire", details={"items": reaffectes})
            flash(f"{reaffectes} matériel(s) rattachés à un emplacement réel.", "success")
        else:
            flash("Rien à reprendre : aucune correspondance choisie.", "info")
        return redirect(url_for("salles.inventaire_reprise"))

    groupes = (
        db.session.query(
            db.func.lower(db.func.trim(InventaireItem.localisation)).label("libelle"),
            db.func.count(InventaireItem.id).label("nb"),
        )
        .filter(InventaireItem.espace_id.is_(None))
        .filter(InventaireItem.localisation.isnot(None), InventaireItem.localisation != "")
        .group_by("libelle")
        .order_by(db.func.count(InventaireItem.id).desc())
        .all()
    )
    return render_template(
        "salles/inventaire_reprise.html", groupes=groupes, emplacements=emplacements,
    )


@bp.route("/reconcilier", methods=["POST"])
@login_required
@require_perm("salles:edit")
def reconcilier():
    """Filet de sécurité : reconstruit les occupations depuis les séances."""
    compteurs = reconcilier_occupations()
    journaliser("salles.reconciliation", details=compteurs)
    flash(
        f"Plannings reconstruits : {compteurs['seances']} séance(s), "
        f"{compteurs['creneaux']} créneau(x), {compteurs['orphelines']} ligne(s) obsolète(s) nettoyée(s).",
        "success",
    )
    return redirect(url_for("salles.index"))


# ---------------------------------------------------------------------------
# Plannings : ce qu'on affiche au mur et ce qu'on pose sur la table
# ---------------------------------------------------------------------------

def _site_courant(site_id: int | None) -> Site | None:
    if site_id:
        return Site.query.get_or_404(site_id)
    return Site.query.filter(Site.actif.is_(True)).order_by(Site.id).first()


@bp.route("/planning")
@login_required
@require_perm("salles:view")
def planning():
    """La grille murale : une ligne par salle, une colonne par jour."""
    site = _site_courant(request.args.get("site_id", type=int))
    if site is None:
        flash("Crée d'abord un site pour voir un planning.", "warning")
        return redirect(url_for("salles.index"))

    jour = _date_arg("semaine") or date.today()
    louables_seulement = request.args.get("louables") == "1"
    donnees = planning_semaine(site.id, jour, louables_seulement=louables_seulement)
    return render_template(
        "salles/planning_semaine.html",
        site=site, sites=Site.query.filter(Site.actif.is_(True)).order_by(Site.nom).all(),
        impression=request.args.get("impression") == "1",
        louables_seulement=louables_seulement,
        aujourdhui=date.today(),
        precedente=(donnees["debut"] - timedelta(days=7)).isoformat(),
        suivante=(donnees["debut"] + timedelta(days=7)).isoformat(),
        **donnees,
    )


@bp.route("/planning/mois")
@login_required
@require_perm("salles:view")
def planning_mensuel():
    """Le calendrier du mois, pour une salle ou pour tout le site."""
    site = _site_courant(request.args.get("site_id", type=int))
    if site is None:
        flash("Crée d'abord un site pour voir un planning.", "warning")
        return redirect(url_for("salles.index"))

    repere = _date_arg("mois") or date.today().replace(day=1)
    espace_id = request.args.get("espace_id", type=int)
    donnees = planning_mois(repere.year, repere.month, espace_id=espace_id, site_id=site.id)

    precedent = (repere.replace(day=1) - timedelta(days=1)).replace(day=1)
    suivant = (repere.replace(day=28) + timedelta(days=7)).replace(day=1)
    return render_template(
        "salles/planning_mois.html",
        site=site, sites=Site.query.filter(Site.actif.is_(True)).order_by(Site.nom).all(),
        salles=[e for e in espaces_reservables() if e.site_id == site.id],
        espace_id=espace_id,
        impression=request.args.get("impression") == "1",
        aujourdhui=date.today(),
        precedent=precedent.isoformat(), suivant=suivant.isoformat(),
        libelle_mois=f"{MOIS_FR[donnees['mois'] - 1]} {donnees['annee']}",
        **donnees,
    )


@bp.route("/aujourdhui")
@login_required
@require_perm("salles:view")
def planning_jour():
    """L'écran du hall : ce qui se passe aujourd'hui, en gros caractères."""
    site = _site_courant(request.args.get("site_id", type=int))
    if site is None:
        flash("Crée d'abord un site.", "warning")
        return redirect(url_for("salles.index"))
    jour = _date_arg("jour") or date.today()
    return render_template(
        "salles/planning_jour.html",
        site=site, jour=jour, lignes=planning_du_jour(site.id, jour),
        ferie=jour_ferie(jour), libelle=libelle_jour(jour),
        plein_ecran=request.args.get("ecran") == "1",
    )


# ---------------------------------------------------------------------------
# Indisponibilités : travaux, fermeture, salle réquisitionnée
# ---------------------------------------------------------------------------

@bp.route("/blocage", methods=["GET", "POST"])
@login_required
@require_perm("salles:edit")
def blocage_redirection():
    """Ancienne adresse de l'écran, conservée pour les liens déjà partagés.

    Une vraie redirection plutôt qu'une seconde règle sur le même endpoint :
    avec deux règles, ``url_for`` choisit l'ancienne et tous les liens de
    l'application continueraient d'afficher « blocage ».
    """
    return redirect(url_for("salles.occuper_form", **request.args))


@bp.route("/occuper", methods=["GET", "POST"])
@login_required
@require_perm("salles:edit")
def occuper_form():
    """Poser une occupation à la main : « cette salle, ce jour, ce créneau ».

    Le geste le plus courant du planning, et celui qu'on fait depuis une
    case vide : une activité, une réunion, une association qui vient, ou
    simplement une salle inutilisable. Les paramètres d'URL permettent
    d'arriver ici avec la salle, le jour et l'horaire déjà remplis — pour
    ne jamais retaper ce qu'on avait sous les yeux.

    Ce n'est PAS une réservation : ni preneur, ni tarif, ni contrat. C'est
    le geste « je note que c'est pris ».
    """
    salles = espaces_reservables()

    def _afficher(**surcharges):
        contexte = {
            "salles": salles,
            "aujourdhui": date.today(),
            "origines": ORIGINES_MANUELLES,
            "origines_labels": ORIGINES_OCCUPATION_LABELS,
            "origines_aide": ORIGINES_MANUELLES_AIDE,
            "couleurs": COULEURS_ORIGINE,
            # Pré-remplissage depuis un clic sur le planning.
            "espace_prefere": request.values.get("espace_id", type=int),
            "jour_prefere": (_date_arg("jour") or date.today()).isoformat(),
            "debut_prefere": request.values.get("debut") or "09:00",
            "fin_prefere": request.values.get("fin") or "12:00",
            "retour": request.values.get("retour") or "",
        }
        contexte.update(surcharges)
        return render_template("salles/occuper_form.html", **contexte)

    if request.method == "POST":
        espace_id = _entier("espace_id")
        jour_debut = _date("date_debut")
        jour_fin = _date("date_fin") or jour_debut
        titre = _texte("titre") or "Occupé"
        origine = _texte("origine", "interne")
        if origine not in ORIGINES_MANUELLES:
            origine = "interne"
        journee_entiere = _case("journee_entiere")
        debut = "00:00" if journee_entiere else _texte("heure_debut", "09:00")
        fin = "23:59" if journee_entiere else _texte("heure_fin", "18:00")

        espace = Espace.query.get(espace_id) if espace_id else None
        if espace is None:
            flash("Choisis la salle occupée.", "danger")
            return _afficher()
        if not jour_debut:
            flash("Il faut au moins une date.", "danger")
            return _afficher()
        if jour_fin < jour_debut:
            flash("La date de fin doit être après la date de début.", "danger")
            return _afficher()
        if (jour_fin - jour_debut).days > 400:
            flash("Une occupation de plus d'un an, ça sent l'erreur de saisie.", "danger")
            return _afficher()
        try:
            m_debut, m_fin = normaliser_plage(debut, fin)
        except SalleErreur as exc:
            flash(str(exc), "danger")
            return _afficher()

        # On prévient des chevauchements AVANT d'enregistrer, en nommant les
        # jours concernés : sur une période longue, « il y a un conflit »
        # sans dire où ne sert à rien.
        genes, cree, jour = [], 0, jour_debut
        while jour <= jour_fin:
            if not _case("ignorer_conflits"):
                for occ in conflits(espace, jour, debut, fin):
                    genes.append(f"{jour.strftime('%d/%m')} ({occ.titre or occ.origine_label})")
                    break
            db.session.add(Occupation(
                espace_id=espace.id, date_jour=jour,
                minute_debut=m_debut, minute_fin=m_fin,
                origine=origine, statut="confirme", titre=titre[:200],
                note=_texte("note") or None,
                effectif_prevu=_entier("effectif_prevu"),
                created_by_user_id=getattr(current_user, "id", None),
            ))
            cree += 1
            jour += timedelta(days=1)

        db.session.commit()
        journaliser("salles.occupation", cible=espace.nom,
                    details={"jours": cree, "titre": titre, "origine": origine})

        message = f"« {espace.nom} » occupée sur {cree} jour(s) : {titre}."
        if espace.enfants:
            message += " Les espaces qu'elle contient le sont aussi."
        flash(message, "success")
        if genes:
            apercu = ", ".join(genes[:5]) + (" …" if len(genes) > 5 else "")
            flash(f"⚠️ Chevauchement avec une occupation existante le {apercu}.", "warning")

        retour = _texte("retour")
        return redirect(retour or url_for("salles.planning", semaine=jour_debut.isoformat()))

    return _afficher()


@bp.route("/occupation/<int:occupation_id>/supprimer", methods=["POST"])
@login_required
@require_perm("salles:edit")
def occupation_supprimer(occupation_id: int):
    """Lever une indisponibilité posée à la main.

    Refuse de toucher aux occupations pilotées par une séance ou un
    créneau : celles-là se modifient à leur source, sinon elles
    réapparaîtraient à la prochaine synchronisation.
    """
    occ = Occupation.query.get_or_404(occupation_id)
    retour = request.form.get("retour") or url_for("salles.planning")
    if occ.pilotee:
        flash(
            "Cette ligne vient d'une séance ou d'un créneau d'agenda : "
            "modifie-la à sa source, sinon elle reviendra toute seule.",
            "warning",
        )
        return redirect(retour)

    titre, jour = occ.titre, occ.date_jour
    db.session.delete(occ)
    db.session.commit()
    journaliser("salles.occupation_suppression", cible=titre, details={"jour": str(jour)})
    flash(f"« {titre or 'Occupation'} » du {jour.strftime('%d/%m/%Y')} levée.", "success")
    return redirect(retour)


# ---------------------------------------------------------------------------
# Horaires d'ouverture
# ---------------------------------------------------------------------------

@bp.route("/site/<int:site_id>/horaires", methods=["GET", "POST"])
@login_required
@require_perm("salles:edit")
def site_horaires(site_id: int):
    """Les heures d'ouverture, jour par jour.

    Sert de garde-fou d'affichage : on prévient quand un créneau sort des
    horaires, sans jamais l'interdire — une AG un samedi soir, ça existe.
    """
    site = Site.query.get_or_404(site_id)

    if request.method == "POST":
        horaires = {}
        for jour in JOURS_SEMAINE_ORDRE:
            if _case(f"ouvert_{jour}"):
                horaires[jour] = [_texte(f"debut_{jour}", "09:00"), _texte(f"fin_{jour}", "18:00")]
            else:
                horaires[jour] = None
        site.horaires_json = ecrire_horaires(horaires)
        db.session.commit()
        journaliser("salles.horaires", cible=site.code)
        flash("Horaires d'ouverture enregistrés.", "success")
        return redirect(url_for("salles.index"))

    horaires = lire_horaires(site) or dict(HORAIRES_DEFAUT)
    return render_template(
        "salles/site_horaires.html",
        site=site, horaires=horaires, jours=JOURS_SEMAINE_ORDRE,
        feries_annee=sorted(jours_feries(date.today().year).items()),
    )
