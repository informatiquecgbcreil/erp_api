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
    SalleErreur,
    arbre_du_site,
    espaces_stockage,
    normaliser_plage,
    recherche_disponibilite,
    reconcilier_occupations,
    zone_conflit_ids,
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
