"""Jeu de départ : le plan d'un centre social, prêt à corriger.

Saisir trente espaces un par un avant même de pouvoir essayer le module,
c'est le meilleur moyen de ne jamais l'essayer. On propose donc une
structure complète en un clic ; il ne reste qu'à ajuster les capacités et
à cocher ce qui est louable.

Rien n'est figé : tout se renomme, se déplace et se supprime ensuite.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Espace, Site

#: Chaque entrée : (clé, parent, nom, type, options).
#: Les options reprennent les drapeaux du modèle ; tout ce qui n'est pas
#: précisé prend la valeur par défaut (réservable, non louable, non stockage).
PLAN_CENTRE_SOCIAL: list[tuple[str, str | None, str, str, dict]] = [
    ("hall",        None, "Hall d'accueil", "circulation", {"louable": False}),

    ("info",        None, "Salle informatique", "salle",
     {"stockage": True, "capacite_usage": 12, "equipements": "Postes informatiques, vidéoprojecteur"}),

    ("atelier",     None, "Atelier", "atelier", {"stockage": True}),
    ("atelier_arm", "atelier", "Armoire sécurisée n°1", "stockage",
     {"reservable": False, "louable": False, "stockage": True, "securise": True}),

    ("cours1",      None, "Salle de cours 1", "salle", {"louable": True}),
    ("cours2",      None, "Salle de cours 2", "salle", {"louable": True}),
    ("cours3",      None, "Salle de cours 3", "salle", {"louable": True}),
    ("cours4",      None, "Salle de cours 4", "salle", {"louable": True}),

    # Cloisons amovibles : la grande salle est le PARENT de ses deux moitiés.
    # C'est cette seule mise en arbre qui rend les conflits automatiques.
    ("grande",      None, "Grande salle d'activité", "salle",
     {"louable": True, "battement_minutes": 30,
      "notes": "Séparable en deux par cloisons amovibles. Réserver une moitié "
               "rend la salle entière indisponible, et inversement."}),
    ("grande_a",    "grande", "Grande salle — partie A", "salle", {"louable": True, "battement_minutes": 30}),
    ("grande_b",    "grande", "Grande salle — partie B", "salle", {"louable": True, "battement_minutes": 30}),

    ("cuisine_pro", None, "Cuisine professionnelle", "cuisine",
     {"louable": True, "battement_minutes": 60,
      "habilitation_requise": "Règles d'hygiène HACCP — à vérifier avant toute mise à disposition"}),

    # --- Aile petite enfance ---------------------------------------------
    ("pe",          None, "Espace petite enfance", "zone", {"louable": False}),
    ("pe_garderie", "pe", "Salle de garderie", "salle", {"louable": False}),
    ("pe_dortoir",  "pe", "Dortoir", "salle", {"louable": False}),
    ("pe_change",   "pe", "Espace WC / change", "sanitaire", {"reservable": False, "louable": False}),
    ("pe_cuisine",  "pe", "Espace cuisine", "cuisine", {"louable": False}),
    ("pe_snoez",    "pe", "Espace snoezelen", "salle",
     {"louable": False, "notes": "Usage variable (détente sensorielle, accompagnement individuel…)."}),
    # Bureau, WC et vestiaire de l'aile : ni occupables, ni louables.
    ("pe_bureau",   "pe", "Bureau petite enfance", "bureau", {"reservable": False, "louable": False}),
    ("pe_wc",       "pe", "WC petite enfance", "sanitaire", {"reservable": False, "louable": False}),
    ("pe_vest",     "pe", "Vestiaire petite enfance", "sanitaire", {"reservable": False, "louable": False}),
    ("pe_ext",      "pe", "Espace extérieur petite enfance", "exterieur", {"louable": False}),

    # --- Bureaux et locaux de l'équipe ------------------------------------
    ("bur1", None, "Bureau 1", "bureau", {"louable": False}),
    ("bur2", None, "Bureau 2", "bureau", {"louable": False}),
    ("bur3", None, "Bureau 3", "bureau", {"louable": False}),
    ("bur4", None, "Bureau 4", "bureau", {"louable": False}),
    ("bur5", None, "Bureau 5", "bureau", {"louable": False}),
    ("bur6", None, "Bureau 6", "bureau", {"louable": False}),
    ("bur7", None, "Bureau 7", "bureau", {"louable": False}),
    ("bur8", None, "Bureau 8", "bureau", {"louable": False}),

    ("cuisine_perso", None, "Cuisine du personnel", "cuisine", {"reservable": False, "louable": False}),
    ("douche",        None, "Espace douche", "sanitaire", {"reservable": False, "louable": False}),
    ("wc_perso",      None, "WC personnel", "sanitaire", {"reservable": False, "louable": False}),

    ("wc_pub1", None, "WC public 1", "sanitaire", {"reservable": False, "louable": False}),
    ("wc_pub2", None, "WC public 2", "sanitaire", {"reservable": False, "louable": False}),
    ("wc_pub3", None, "WC public 3", "sanitaire", {"reservable": False, "louable": False}),

    ("exterieur", None, "Espace extérieur", "exterieur", {"louable": True}),
]


def installer_plan(site: Site, plan=None) -> int:
    """Crée les espaces du plan dans un site VIDE. Retourne le nombre créé.

    Ne fait rien si le site contient déjà des espaces : on ne veut surtout
    pas dupliquer un plan déjà retouché à la main.
    """
    if Espace.query.filter(Espace.site_id == site.id).count():
        return 0

    modele = plan if plan is not None else PLAN_CENTRE_SOCIAL
    crees: dict[str, Espace] = {}

    for rang, (cle, parent_cle, nom, type_espace, options) in enumerate(modele):
        espace = Espace(
            site=site,
            parent=crees.get(parent_cle) if parent_cle else None,
            nom=nom,
            type_espace=type_espace,
            ordre=rang * 10,
            reservable=options.get("reservable", True),
            louable=options.get("louable", False),
            stockage=options.get("stockage", False),
            securise=options.get("securise", False),
            battement_minutes=options.get("battement_minutes", 0),
            capacite_usage=options.get("capacite_usage"),
            capacite_reglementaire=options.get("capacite_reglementaire"),
            habilitation_requise=options.get("habilitation_requise"),
            equipements=options.get("equipements"),
            notes=options.get("notes"),
        )
        # Un espace louable est forcément réservable : on ne met pas à
        # disposition d'un tiers un lieu qui n'apparaît nulle part.
        if espace.louable:
            espace.reservable = True
        crees[cle] = espace
        db.session.add(espace)

    db.session.commit()
    return len(crees)
