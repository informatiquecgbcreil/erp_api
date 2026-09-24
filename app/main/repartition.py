"""Où va la participation : la répartition par secteur, à l'écran.

L'adhésion couvre l'assurance — c'est la part légale, elle ne se découpe
pas. La participation finance les secteurs, et elle se répartit désormais
au prorata des venues réelles de chaque personne.

Encore faut-il que quatre métiers différents y lisent la même chose sans
se le faire expliquer :

- le **référent de secteur** veut un seul chiffre : ce qu'il peut engager ;
- l'**accueil** veut comprendre, devant la personne, d'où sort sa part ;
- la **direction** veut la vue d'ensemble et l'équilibre entre secteurs ;
- la **comptabilité** veut que les colonnes totalisent, au centime.

D'où le parti pris de l'écran : l'encaissé en gros, le dû à côté, et la
distinction entre **répartition vivante** (qui bougera jusqu'au 31 août)
et **arrêté figé** affichée de façon impossible à manquer. Confondre les
deux est la seule erreur vraiment coûteuse ici : on n'engage pas un budget
sur un chiffre provisoire.
"""
from datetime import date

from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.main.common import bp
from app.models import RepartitionArretee
from app.extensions import db
from app.rbac import require_perm
from app.secteurs import get_secteur_labels
from app.services.cotisations import (
    annee_scolaire_courante,
    annees_scolaires_disponibles,
    libelle_annee_scolaire,
)
from app.services.prorata import (
    arreter,
    arretes,
    canoniser,
    export_xlsx,
    periode_couverte,
    repartition,
    repartition_periode,
)


def _a_vue_globale() -> bool:
    return (current_user.has_perm("scope:all_secteurs")
           )


def _secteur_impose() -> str | None:
    """Le secteur auquel l'utilisateur est borné, ou None s'il voit tout.

    Même logique que la liste des participants et l'écran des impayés : un
    référent voit son secteur, pas la comptabilité de ses collègues.
    """
    if _a_vue_globale():
        return None
    from app.services.access_scope import effective_sector
    return effective_sector()


def _vue_depuis_un_arrete(arrete: RepartitionArretee) -> dict:
    """Présente un arrêté figé avec la même forme qu'une répartition vivante.

    Un seul gabarit pour les deux : deux rendus divergents de la même
    information, c'est deux vérités, et c'est la confusion garantie entre
    le provisoire et le définitif.
    """
    personnes: dict[int, dict] = {}
    for ligne in arrete.lignes:
        cle = ligne.participant_id or -ligne.id
        entree = personnes.setdefault(cle, {
            "nom": ligne.nom_affiche,
            "participant_id": ligne.participant_id,
            "du": 0.0, "regle": 0.0, "total_venues": 0,
            "repli": bool(ligne.repli), "parts": {},
        })
        entree["parts"][ligne.secteur] = {
            "venues": int(ligne.venues or 0),
            "du": round(float(ligne.montant_du or 0), 2),
            "regle": round(float(ligne.montant_regle or 0), 2),
        }
        entree["du"] = round(entree["du"] + float(ligne.montant_du or 0), 2)
        entree["regle"] = round(entree["regle"] + float(ligne.montant_regle or 0), 2)
        entree["total_venues"] += int(ligne.venues or 0)

    liste = sorted(personnes.values(), key=lambda p: (p["nom"] or "").lower())
    secteurs = arrete.totaux_par_secteur()
    debut, fin = periode_couverte(arrete.annee_scolaire, arrete.date_arrete)
    return {
        "annee_scolaire": arrete.annee_scolaire,
        "a_la_date": arrete.date_arrete,
        "periode": {"debut": debut, "fin": fin},
        "libelle_annee": arrete.libelle_annee,
        "personnes": liste,
        "secteurs": secteurs,
        "totaux": {
            "du": round(arrete.total_du or 0, 2),
            "regle": round(arrete.total_regle or 0, 2),
            "nb_personnes": len(liste),
            "nb_repli": sum(1 for p in liste if p["repli"]),
            "venues": sum(case["venues"] for case in secteurs.values()),
        },
    }


def _normaliser(vue: dict) -> dict:
    """Donne la même forme aux deux sources, pour un seul gabarit.

    Le calcul vivant rend l'objet Participant ; un arrêté n'a plus qu'un nom
    figé, parce que la fiche a pu disparaître depuis. Le gabarit ne doit
    connaître que ``nom`` et ``participant_id``.
    """
    for personne in vue.get("personnes", []):
        fiche = personne.get("participant")
        if fiche is not None:
            personne.setdefault(
                "nom", f"{fiche.nom or ''} {fiche.prenom or ''}".strip())
            personne.setdefault("participant_id", fiche.id)
    return vue


def _borner_au_secteur(vue: dict, secteur: str) -> dict:
    """Ne garder qu'un secteur : ses totaux, et les personnes qui y viennent."""
    officiel = canoniser(secteur)
    vue = dict(vue)
    vue["secteurs"] = {
        nom: case for nom, case in vue["secteurs"].items() if nom == officiel
    }
    vue["personnes"] = [p for p in vue["personnes"] if officiel in p["parts"]]
    case = vue["secteurs"].get(officiel, {"du": 0.0, "regle": 0.0, "venues": 0})
    vue["totaux"] = dict(vue["totaux"])
    vue["totaux"].update({
        "du": case["du"], "regle": case["regle"],
        "nb_personnes": len(vue["personnes"]), "venues": case["venues"],
    })
    return vue


def _contexte_demande():
    """Ce que l'écran ET l'export doivent lire de la requête.

    Une seule construction pour les deux : un export qui ne dirait pas la
    même chose que l'écran d'où on l'a cliqué serait pire qu'inutile.
    Retourne ``(annee, vue, arrete_affiche, secteur_impose, filtre)``.
    """
    try:
        annee = int(request.args.get("annee") or annee_scolaire_courante())
    except (TypeError, ValueError):
        annee = annee_scolaire_courante()

    # Période libre : deux dates valides suffisent à basculer de mode. Elle
    # existe parce que les cotisations vivent en année SCOLAIRE tandis qu'un
    # exercice comptable peut suivre l'année CIVILE — sans elle, la
    # comptabilité recomposerait son 1er janvier - 31 décembre à la main
    # depuis deux années scolaires.
    bornes = []
    for champ in ("debut", "fin"):
        brut = (request.args.get(champ) or "").strip()
        try:
            bornes.append(date.fromisoformat(brut) if brut else None)
        except ValueError:
            bornes.append(None)
    debut_libre, fin_libre = bornes
    if debut_libre and fin_libre:
        vue = _normaliser(repartition_periode(debut_libre, fin_libre))
        impose = _secteur_impose()
        if impose:
            vue = _borner_au_secteur(vue, impose)
        filtre = (request.args.get("secteur") or "").strip() or None
        if filtre and not impose:
            vue = _borner_au_secteur(vue, filtre)
        return annee, vue, None, impose, filtre

    # « vivant » (défaut) ou l'identifiant d'un arrêté figé.
    choix = (request.args.get("arrete") or "").strip()
    arrete_affiche = None
    if choix and choix != "vivant":
        try:
            arrete_affiche = db.session.get(RepartitionArretee, int(choix))
        except (TypeError, ValueError):
            arrete_affiche = None
        if arrete_affiche is not None and arrete_affiche.annee_scolaire != annee:
            arrete_affiche = None

    vue = _normaliser(
        _vue_depuis_un_arrete(arrete_affiche) if arrete_affiche is not None
        else repartition(annee))

    impose = _secteur_impose()
    if impose:
        vue = _borner_au_secteur(vue, impose)

    # Filtre secteur volontaire (vue globale seulement).
    filtre = (request.args.get("secteur") or "").strip() or None
    if filtre and not impose:
        vue = _borner_au_secteur(vue, filtre)

    return annee, vue, arrete_affiche, impose, filtre


@bp.route("/repartition-participation")
@login_required
@require_perm("cotisations:view")
def repartition_participation():
    annee, vue, arrete_affiche, impose, filtre = _contexte_demande()
    liste_arretes = arretes(annee)

    return render_template(
        "repartition_participation.html",
        vue=vue,
        annee=annee,
        libelle_annee=libelle_annee_scolaire(annee),
        annees=sorted(set(annees_scolaires_disponibles()) | {annee}, reverse=True),
        secteurs=get_secteur_labels(active_only=True),
        secteur_filtre=impose or filtre,
        peut_choisir_secteur=not impose,
        secteur_impose=impose,
        arretes=liste_arretes,
        arrete_affiche=arrete_affiche,
        peut_arreter=current_user.has_perm("cotisations:edit") and not impose,
        aujourdhui=date.today(),
        debut_libre=(request.args.get("debut") or "").strip(),
        fin_libre=(request.args.get("fin") or "").strip(),
    )


@bp.route("/repartition-participation.xlsx")
@login_required
@require_perm("cotisations:view")
def repartition_participation_xlsx():
    """Le même tableau, en classeur — et il porte sa propre provenance.

    Un tableur circule par courriel, détaché de l'écran qui l'a produit :
    l'en-tête dit en toutes lettres s'il s'agit d'un provisoire ou d'un
    arrêté, et un onglet « Contrôle » rapproche les colonnes du total.
    """
    annee, vue, arrete_affiche, impose, filtre = _contexte_demande()
    classeur = export_xlsx(vue, arrete=arrete_affiche)

    if vue.get("mode") == "periode":
        bornes = vue["periode"]
        morceaux = ["repartition_participation", "periode",
                    f"{bornes['debut']:%Y%m%d}_{bornes['fin']:%Y%m%d}"]
    else:
        morceaux = ["repartition_participation", libelle_annee_scolaire(annee)]
        if arrete_affiche is not None:
            morceaux.append(f"arrete_{arrete_affiche.date_arrete:%Y%m%d}")
        else:
            morceaux.append(f"provisoire_{date.today():%Y%m%d}")
    secteur = impose or filtre
    if secteur:
        morceaux.append(secteur.replace(" ", "_")[:40])

    return send_file(
        classeur,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="_".join(morceaux) + ".xlsx",
    )


@bp.route("/repartition-participation/arreter", methods=["POST"])
@login_required
@require_perm("cotisations:edit")
def repartition_arreter():
    """Fige la répartition à une date. Le service refuse ce qui n'a pas de sens."""
    try:
        annee = int(request.form.get("annee") or annee_scolaire_courante())
    except (TypeError, ValueError):
        annee = annee_scolaire_courante()

    brut = (request.form.get("date_arrete") or "").strip()
    try:
        jour = date.fromisoformat(brut)
    except ValueError:
        flash("Date d'arrêté invalide.", "danger")
        return redirect(url_for("main.repartition_participation", annee=annee))

    arrete, message = arreter(
        annee, jour,
        libelle=request.form.get("libelle"),
        note=request.form.get("note"),
        user_id=getattr(current_user, "id", None),
    )
    flash(message, "success" if arrete is not None else "danger")
    return redirect(url_for(
        "main.repartition_participation", annee=annee,
        arrete=arrete.id if arrete is not None else "vivant"))


@bp.route("/repartition-participation/arrete/<int:arrete_id>/supprimer", methods=["POST"])
@login_required
@require_perm("cotisations:edit")
def repartition_arrete_supprimer(arrete_id: int):
    """Supprime un arrêté — le seul moyen d'en refaire un à la même date."""
    arrete = db.get_or_404(RepartitionArretee, arrete_id)
    annee = arrete.annee_scolaire
    intitule = arrete.intitule
    db.session.delete(arrete)
    db.session.commit()
    flash(f"Arrêté supprimé : {intitule}.", "success")
    return redirect(url_for("main.repartition_participation", annee=annee))
