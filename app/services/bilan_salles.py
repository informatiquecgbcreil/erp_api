"""Taux d'occupation et valorisation des mises à disposition.

Deux chiffres que les financeurs réclament et que personne n'a jamais sous
la main :

- « Vos locaux sont-ils utilisés ? » — le taux d'occupation, salle par
  salle, rapporté aux heures réellement ouvrables (horaires d'ouverture
  du centre, fériés déduits). Un taux calculé sur 24 h par jour ne veut
  rien dire et se retourne contre la structure.

- « Que vaut ce que vous recevez, et ce que vous donnez ? » — la
  valorisation des contributions volontaires en nature, dans les deux
  sens : le bâtiment mis à disposition par la collectivité, et les
  gratuités consenties au tissu associatif. Les deux ont leur place au
  compte de résultat (comptes 86/87 du plan comptable associatif).

Tout se déduit des occupations déjà enregistrées : rien à ressaisir, et
les chiffres bougent tout seuls au fil de l'année.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date
from datetime import timedelta

from app.extensions import db
from app.models import (
    ORIGINES_OCCUPATION_LABELS,
    Espace,
    Occupation,
    Reservation,
    Site,
    minutes_depuis_texte,
)

#: Regroupement des origines pour la lecture. Qui lit un bilan se moque de
#: savoir si l'activité vient du module Activités ou a été tapée à la main.
FAMILLES = {
    "seance": "activites",
    "interne": "activites",
    "creneau": "equipe",
    "reunion": "equipe",
    "location": "tiers",
    "blocage": "indisponible",
}
FAMILLES_LABELS = {
    "activites": "Activités avec du public",
    "equipe": "Réunions et temps d'équipe",
    "tiers": "Mises à disposition de tiers",
    "indisponible": "Indisponibilités",
}
#: L'indisponibilité ne compte pas comme une occupation : une salle en
#: travaux n'est pas « utilisée », elle est retirée du potentiel.
FAMILLES_OCCUPANTES = ["activites", "equipe", "tiers"]

#: Ce qu'on entend par « espace d'activité » : ce qui a vocation à être
#: programmé. Un bureau d'équipe est occupable — on y pose des réunions —
#: mais l'inclure dans un taux global le ferait plonger sans rien dire de
#: l'usage réel des locaux. Un centre social qui annonce 1,5 % d'occupation
#: parce qu'on a divisé par huit bureaux se discrédite tout seul.
TYPES_ACTIVITE = ["salle", "atelier", "cuisine", "exterieur", "zone"]

PERIMETRES = ["activite", "louables", "tous"]
PERIMETRES_LABELS = {
    "activite": "Espaces d'activité",
    "louables": "Salles louables uniquement",
    "tous": "Tous les espaces occupables",
}
PERIMETRES_AIDE = {
    "activite": "Salles, ateliers, cuisines et extérieurs — ce qui a vocation à être programmé.",
    "louables": "Uniquement ce qui peut être mis à disposition d'un tiers.",
    "tous": "Y compris bureaux et locaux d'équipe : le taux global perd alors son sens.",
}


def _heures(minutes: float) -> float:
    return round(minutes / 60.0, 1)


def heures_ouvrables(site: Site, debut: Date, fin: Date) -> float:
    """Le potentiel réel d'une salle sur la période.

    Fondé sur les horaires d'ouverture du site, fériés déduits. Sans
    horaires renseignés on retient 8 h par jour ouvré plutôt que 24 h : un
    dénominateur absurde produit un taux ridicule, et c'est le genre de
    chiffre qui décrédibilise tout un bilan.
    """
    from app.services.temps_ouverture import horaires_du_jour, lire_horaires

    horaires_saisis = bool(lire_horaires(site))
    total, jour = 0.0, debut
    while jour <= fin:
        plage = horaires_du_jour(site, jour)
        if plage is None:
            jour += timedelta(days=1)
            continue
        if not horaires_saisis:
            total += 8.0 if jour.weekday() < 5 else 0.0
        else:
            ouverture = minutes_depuis_texte(plage[0])
            fermeture = minutes_depuis_texte(plage[1])
            if ouverture is not None and fermeture is not None and fermeture > ouverture:
                total += (fermeture - ouverture) / 60.0
        jour += timedelta(days=1)
    return round(total, 1)


def _salles_du_perimetre(site_id: int, perimetre: str):
    """Les espaces retenus pour le calcul, selon le périmètre demandé."""
    q = Espace.query.filter(
        Espace.site_id == site_id, Espace.reservable.is_(True), Espace.actif.is_(True)
    )
    if perimetre == "louables":
        q = q.filter(Espace.louable.is_(True))
    elif perimetre != "tous":
        q = q.filter(Espace.type_espace.in_(TYPES_ACTIVITE))
    return q.order_by(Espace.ordre, Espace.nom).all()


def occupation_par_salle(site_id: int, debut: Date, fin: Date,
                         perimetre: str = "activite") -> list[dict]:
    """Une ligne par salle : heures occupées, potentiel, taux, répartition.

    Seules les occupations DIRECTES comptent. Une réservation de demi-salle
    rend la grande salle indisponible, mais ne l'« occupe » pas : la
    compter des deux côtés gonflerait artificiellement le taux global.
    """
    site = db.session.get(Site, site_id)
    if site is None:
        return []

    potentiel = heures_ouvrables(site, debut, fin)
    salles = _salles_du_perimetre(site_id, perimetre)
    ids = {s.id for s in salles}

    occupations = (
        Occupation.query
        .filter(Occupation.espace_id.in_(ids or {0}))
        .filter(Occupation.date_jour >= debut, Occupation.date_jour <= fin)
        .filter(Occupation.statut != "annule")
        .all()
    )

    minutes = defaultdict(lambda: defaultdict(float))
    comptes = defaultdict(lambda: defaultdict(int))
    for occ in occupations:
        famille = FAMILLES.get(occ.origine, "activites")
        minutes[occ.espace_id][famille] += occ.duree_minutes
        comptes[occ.espace_id][famille] += 1

    lignes = []
    for salle in salles:
        par_famille = minutes[salle.id]
        occupees = sum(par_famille[f] for f in FAMILLES_OCCUPANTES)
        indispo = par_famille.get("indisponible", 0.0)
        # Une salle en travaux sort du potentiel : elle n'avait pas à être
        # utilisée, la compter comme un échec serait injuste.
        potentiel_net = max(0.0, potentiel - _heures(indispo))
        lignes.append({
            "espace": salle,
            "heures": _heures(occupees),
            "potentiel": round(potentiel_net, 1),
            "taux": round(100 * _heures(occupees) / potentiel_net, 1) if potentiel_net else 0.0,
            # Indexé par FAMILLE et non par origine : FAMILLES fait
            # correspondre origine → famille, l'itérer donnerait les clés
            # d'entrée alors qu'on regroupe justement pour s'en affranchir.
            "familles": {f: _heures(par_famille.get(f, 0.0)) for f in FAMILLES_LABELS},
            "seances": {f: comptes[salle.id].get(f, 0) for f in FAMILLES_LABELS},
            "indisponible": _heures(indispo),
        })
    lignes.sort(key=lambda l: (-l["taux"], l["espace"].nom.lower()))
    return lignes


def valorisation(site_id: int, debut: Date, fin: Date) -> dict:
    """Ce que la structure reçoit, et ce qu'elle donne.

    Reçu : la valeur locative du bâtiment mis à disposition, au prorata de
    la période. Donné : les gratuités consenties, chiffrées au barème —
    c'est exactement ce que la structure apporte au territoire, et ça ne
    se voit nulle part si personne ne le calcule.
    """
    site = db.session.get(Site, site_id)
    if site is None:
        return {}

    jours = (fin - debut).days + 1
    prorata = jours / 365.0

    valeur_site = float(site.valeur_locative_annuelle or 0.0)
    valeur_espaces = sum(
        float(e.valeur_locative_annuelle or 0.0)
        for e in Espace.query.filter(Espace.site_id == site_id).all()
    )
    # La somme des espaces prime quand elle est renseignée : elle est plus
    # fine que l'estimation globale, et les deux ne s'additionnent pas.
    recu_annuel = valeur_espaces or valeur_site

    reservations = (
        Reservation.query
        .filter(Reservation.espace_id.in_(
            db.session.query(Espace.id).filter(Espace.site_id == site_id)
        ))
        .filter(Reservation.statut.in_(["confirmee", "realisee"]))
        .all()
    )

    donne, encaisse, heures_gratuites, preneurs = 0.0, 0.0, 0.0, set()
    for r in reservations:
        dates = [o.date_jour for o in r.occupations or [] if o.statut != "annule"]
        if not dates or max(dates) < debut or min(dates) > fin:
            continue
        preneurs.add(r.preneur_id)
        heures = sum(
            o.duree_minutes for o in r.occupations
            if o.statut != "annule" and debut <= o.date_jour <= fin
        ) / 60.0
        if r.gratuite:
            donne += float(r.montant_calcule or 0.0)
            heures_gratuites += heures
        else:
            encaisse += r.montant_du
            ecart = r.ecart_au_bareme
            if ecart and ecart < 0:
                donne += abs(ecart)  # un rabais consenti est aussi une contribution

    return {
        "jours": jours,
        "recu_annuel": round(recu_annuel, 2),
        "recu_periode": round(recu_annuel * prorata, 2),
        "donne": round(donne, 2),
        "encaisse": round(encaisse, 2),
        "heures_gratuites": round(heures_gratuites, 1),
        "structures_accueillies": len(preneurs),
        "source_valeur": "espaces" if valeur_espaces else ("site" if valeur_site else None),
    }


def synthese(site_id: int, debut: Date, fin: Date, perimetre: str = "activite") -> dict:
    """Tout ce qu'il faut pour l'écran et l'export, en une passe."""
    if perimetre not in PERIMETRES:
        perimetre = "activite"
    lignes = occupation_par_salle(site_id, debut, fin, perimetre)
    valeurs = valorisation(site_id, debut, fin)

    total_heures = round(sum(l["heures"] for l in lignes), 1)
    total_potentiel = round(sum(l["potentiel"] for l in lignes), 1)
    par_famille = {
        f: round(sum(l["familles"].get(f, 0.0) for l in lignes), 1) for f in FAMILLES_LABELS
    }
    return {
        "lignes": lignes,
        "perimetre": perimetre,
        "perimetre_label": PERIMETRES_LABELS[perimetre],
        "nb_salles": len(lignes),
        "valorisation": valeurs,
        "total_heures": total_heures,
        "total_potentiel": total_potentiel,
        "taux_global": round(100 * total_heures / total_potentiel, 1) if total_potentiel else 0.0,
        "par_famille": par_famille,
        "salles_jamais_utilisees": [l for l in lignes if l["heures"] == 0],
        "debut": debut,
        "fin": fin,
    }


def phrase_pour_dossier(site: Site, donnees: dict) -> str:
    """La phrase toute faite à recopier dans un dossier de subvention.

    Parce que c'est exactement ce qu'on cherche à 23 h la veille du dépôt,
    et qu'on finit par l'inventer faute de la trouver.
    """
    v = donnees.get("valorisation") or {}
    morceaux = [
        f"Sur la période du {donnees['debut'].strftime('%d/%m/%Y')} au "
        f"{donnees['fin'].strftime('%d/%m/%Y')}, les locaux de {site.nom} ont été "
        f"occupés {donnees['total_heures']:.0f} heures, soit un taux d'occupation "
        f"de {donnees['taux_global']:.0f} % des créneaux ouvrables "
        f"sur {donnees.get('nb_salles', 0)} espace(s) d'activité."
    ]
    if v.get("structures_accueillies"):
        morceaux.append(
            f"{v['structures_accueillies']} structure(s) extérieure(s) y ont été accueillies."
        )
    if v.get("heures_gratuites"):
        morceaux.append(
            f"{v['heures_gratuites']:.0f} heures ont été mises à disposition à titre gratuit, "
            f"représentant une contribution volontaire en nature de {v['donne']:.2f} €."
        )
    if v.get("recu_periode"):
        morceaux.append(
            f"La mise à disposition des locaux par la collectivité est valorisée à "
            f"{v['recu_periode']:.2f} € sur la période."
        )
    return " ".join(morceaux)
