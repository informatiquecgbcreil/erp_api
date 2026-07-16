"""Guides pas à pas : un fil conducteur PAR-DESSUS les écrans existants.

Un guide n'est PAS un formulaire parallèle : il emmène la personne sur les
pages normales de l'application, dans le bon ordre, avec une explication en
français courant à chaque étape. Le guide actif vit dans la session et
s'affiche en bandeau sur toutes les pages jusqu'à ce qu'il soit terminé ou
quitté — on peut donc naviguer librement sans perdre le fil.

Règles :
- une étape pointe vers un écran existant (endpoint) OU explique quoi faire
  sur l'écran où l'application vient d'emmener la personne (endpoint None) ;
- un guide n'est proposé que si la personne a les permissions de ses écrans ;
- tout échec de résolution fait simplement disparaître le bandeau.
"""
from __future__ import annotations

from typing import Any

from flask import session, url_for
from werkzeug.routing import BuildError

from app.rbac import _expand_perm

SESSION_KEY = "guide_actif"


def _user_can_any(user, codes) -> bool:
    has_perm = getattr(user, "has_perm", None)
    if not callable(has_perm):
        return False
    for code in codes or []:
        if any(has_perm(c) for c in _expand_perm(code)):
            return True
    return not codes


GUIDES: dict[str, dict[str, Any]] = {
    "accueillir": {
        "titre": "J'accueille une nouvelle personne",
        "icone": "🤝",
        "resume": "De la première visite à la présence en atelier : vérifier, créer la fiche, la compléter, noter la présence.",
        "perm_any": ["participants:edit"],
        "etapes": [
            {
                "titre": "Vérifier qu'elle n'a pas déjà une fiche",
                "texte": "Tape son nom dans la recherche de la liste. Si sa fiche apparaît, ouvre-la : inutile d'en créer une deuxième, tu peux passer directement à la dernière étape.",
                "endpoint": "participants.list_participants",
            },
            {
                "titre": "Créer sa fiche",
                "texte": "Le nom et le prénom suffisent pour commencer, le reste peut attendre. Pense quand même à poser la question du droit à l'image. En cliquant sur Enregistrer, l'application t'amène sur la fiche créée.",
                "endpoint": "participants.new_participant",
            },
            {
                "titre": "Compléter la fiche",
                "texte": "Tu es maintenant sur sa fiche. Vérifie la ville et le quartier (ils comptent pour les bilans CAF) et ajoute un téléphone si possible.",
                "endpoint": None,
            },
            {
                "titre": "Noter sa présence sur une séance",
                "texte": "Ouvre l'atelier concerné, choisis la séance du jour et coche sa présence. C'est cette présence qui l'inscrit réellement dans l'activité — il n'y a rien d'autre à faire.",
                "endpoint": "activite.index",
            },
        ],
    },
    "faire_appel": {
        "titre": "Je fais l'appel d'une séance",
        "icone": "🖐️",
        "resume": "Retrouver sa séance et noter les présents, ou laisser les gens émarger eux-mêmes.",
        "perm_any": ["emargement:edit"],
        "etapes": [
            {
                "titre": "Ouvrir mon atelier",
                "texte": "Dans la liste des ateliers, clique sur celui qui a lieu aujourd'hui.",
                "endpoint": "activite.index",
            },
            {
                "titre": "Ouvrir la séance du jour",
                "texte": "Dans la page de l'atelier, ouvre la séance d'aujourd'hui. Si elle n'existe pas encore, crée-la : la date du jour est proposée par défaut.",
                "endpoint": None,
            },
            {
                "titre": "Cocher les présents",
                "texte": "Coche chaque personne présente ; tout s'enregistre au fur et à mesure. Astuce : le bouton Kiosque permet de laisser les gens émarger eux-mêmes sur une tablette à l'entrée.",
                "endpoint": None,
            },
        ],
    },
    "rattraper_emargements": {
        "titre": "Je rattrape les émargements papier",
        "icone": "🗓️",
        "resume": "Saisir en masse les feuilles en retard, sans tableur : la grille alimente directement les statistiques.",
        "perm_any": ["emargement:edit"],
        "etapes": [
            {
                "titre": "Voir ce qui manque",
                "texte": "Cette page liste toutes les séances passées sans présence saisie, atelier par atelier, avec le retard en jours. Le bouton Relancer pose un rappel pour courir après une feuille.",
                "endpoint": "activite.emargements_attente",
            },
            {
                "titre": "Saisir en grille",
                "texte": "Choisis l'atelier et le mois de la feuille : les participants sont en lignes, les dates en colonnes, comme dans un tableur. Coche les présents, ajoute une séance ou une personne manquante si besoin, puis Enregistrer.",
                "endpoint": "activite.saisie_grille",
            },
            {
                "titre": "C'est tout — les stats se font toutes seules",
                "texte": "Chaque case cochée alimente directement les statistiques, les bilans et le SENACS. Plus aucun tableau à tenir à jour à côté.",
                "endpoint": None,
            },
        ],
    },
    "compter_caisse": {
        "titre": "Je compte la caisse et je fais le dépôt",
        "icone": "💶",
        "resume": "L'arrêté de caisse puis le dépôt en banque, expliqués pas à pas — même sans jamais l'avoir fait.",
        "perm_any": ["caisse:edit"],
        "etapes": [
            {
                "titre": "Ouvrir la caisse et lire l'état",
                "texte": "Les quatre chiffres du haut disent tout : ce qu'il devrait y avoir dans la boîte (théorique), le fond de caisse (la monnaie qu'on garde toujours), la recette déposable et les chèques en attente. Première fois ? Commence par régler le fond de caisse (bloc de droite).",
                "endpoint": "main.caisse",
            },
            {
                "titre": "Compter les espèces",
                "texte": "Sors tout ce qu'il y a dans la boîte et compte (billets + pièces, fond compris). Saisis le total dans « Compter la caisse » : l'application compare au théorique. Un écart n'est pas un drame — il est tracé, et le théorique repart du réel.",
                "endpoint": None,
            },
            {
                "titre": "Préparer le dépôt",
                "texte": "Dans « Faire un dépôt » : les espèces au-dessus du fond de caisse (le montant maximum est affiché), les chèques avec leur nombre. Le fond de caisse, lui, reste dans la boîte.",
                "endpoint": None,
            },
            {
                "titre": "Imprimer le bordereau et aller à la banque",
                "texte": "Le bordereau s'ouvre tout seul après l'enregistrement : imprime-le, il accompagne les fonds et la banque y met son cachet. C'est terminé — le journal de la caisse garde la trace de tout.",
                "endpoint": None,
            },
        ],
    },
    "preparer_bilan": {
        "titre": "Je prépare un bilan pour un financeur",
        "icone": "📑",
        "resume": "Vérifier les données, lire les chiffres, récupérer les documents prêts à envoyer.",
        "perm_any": ["stats:view"],
        "etapes": [
            {
                "titre": "Vérifier la qualité des données",
                "texte": "Avant de sortir des chiffres, corrige ce que cette page signale : fiches sans quartier, séances sans présence… Ce sont ces trous qui faussent les bilans.",
                "endpoint": "main.qualite_donnees_transverse",
            },
            {
                "titre": "Lire les chiffres",
                "texte": "Cette page rassemble les statistiques attendues par les financeurs : publics touchés, fréquentation, répartition par quartier. Choisis la bonne période en haut de page.",
                "endpoint": "main.stats_bilans",
            },
            {
                "titre": "Récupérer les documents",
                "texte": "Les exports prêts à envoyer (tableaux, synthèses) sont regroupés ici. Télécharge celui qui correspond à ton financeur.",
                "endpoint": "main.documents_exports",
            },
        ],
    },
}


def _safe_url(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    try:
        return url_for(endpoint)
    except BuildError:
        return None


def guides_disponibles(user) -> list[dict[str, Any]]:
    """Les guides que cette personne a le droit de suivre."""
    items = []
    for key, g in GUIDES.items():
        if not _user_can_any(user, g.get("perm_any")):
            continue
        items.append({
            "key": key,
            "titre": g["titre"],
            "icone": g.get("icone") or "🧭",
            "resume": g.get("resume") or "",
            "nb_etapes": len(g["etapes"]),
        })
    return items


def demarrer_guide(key: str) -> str | None:
    """Active le guide en session ; retourne l'URL de la première étape."""
    g = GUIDES.get(key)
    if not g:
        return None
    session[SESSION_KEY] = {"key": key, "etape": 0}
    return _safe_url(g["etapes"][0].get("endpoint"))


def avancer_guide(delta: int = 1) -> str | None:
    """Change d'étape ; retourne l'URL de la nouvelle étape (None si fin/aucune)."""
    etat = session.get(SESSION_KEY) or {}
    g = GUIDES.get(etat.get("key"))
    if not g:
        session.pop(SESSION_KEY, None)
        return None
    etape = int(etat.get("etape") or 0) + delta
    if etape >= len(g["etapes"]):
        session.pop(SESSION_KEY, None)
        return None
    etape = max(0, etape)
    session[SESSION_KEY] = {"key": etat["key"], "etape": etape}
    return _safe_url(g["etapes"][etape].get("endpoint"))


def quitter_guide() -> None:
    session.pop(SESSION_KEY, None)


def guide_actif_ctx() -> dict[str, Any] | None:
    """État du guide actif pour le bandeau du layout (None si aucun)."""
    etat = session.get(SESSION_KEY) or {}
    g = GUIDES.get(etat.get("key"))
    if not g:
        return None
    idx = int(etat.get("etape") or 0)
    if not (0 <= idx < len(g["etapes"])):
        return None
    step = g["etapes"][idx]
    return {
        "key": etat["key"],
        "titre": g["titre"],
        "icone": g.get("icone") or "🧭",
        "etape_num": idx + 1,
        "nb_etapes": len(g["etapes"]),
        "etape_titre": step["titre"],
        "etape_texte": step["texte"],
        "etape_url": _safe_url(step.get("endpoint")),
        "derniere": idx + 1 == len(g["etapes"]),
        "premiere": idx == 0,
    }
