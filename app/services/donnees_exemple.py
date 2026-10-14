"""Le jeu de données d'exemple du mode entraînement.

De quoi tout essayer : une vingtaine d'habitants, trois activités sur les
secteurs actifs, six semaines de séances avec leurs présences, des
adhésions dont certaines réglées en caisse, d'autres pas encore (pour voir
les relances).

Ce jeu ne crée **ni quartier, ni salle, ni tarif** : ces éléments sont
gardés en quittant l'entraînement (ce sont des choix de configuration), un
faux quartier survivrait donc dans la vraie base. Les habitants prennent
les quartiers déjà réglés pour la ville siège, s'il y en a.

Les adhésions et règlements passent par les services de la caisse
(encaissements.enregistrer / ventiler), comme une saisie à l'accueil : les
garde-fous comptables s'appliquent aux fausses données comme aux vraies.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db

#: (prénom, nom, genre, année de naissance)
HABITANTS = [
    ("Léa", "Moreau", "Femme", 1988), ("Karim", "Benali", "Homme", 1975),
    ("Fatou", "Diallo", "Femme", 1992), ("Lucas", "Petit", "Homme", 2009),
    ("Inès", "Haddad", "Femme", 2011), ("Michel", "Lefebvre", "Homme", 1951),
    ("Aïcha", "Traoré", "Femme", 1983), ("Hugo", "Garnier", "Homme", 2014),
    ("Nadia", "Rahmani", "Femme", 1969), ("Thomas", "Roux", "Homme", 1996),
    ("Sofia", "Lopes", "Femme", 2008), ("Mamadou", "Keita", "Homme", 1980),
    ("Chloé", "Fournier", "Femme", 2016), ("Yanis", "Belkacem", "Homme", 2005),
    ("Monique", "Girard", "Femme", 1948), ("Samir", "Ouali", "Homme", 1990),
    ("Emma", "Lambert", "Femme", 2012), ("Bilal", "Saïdi", "Homme", 1999),
    ("Rose", "Mercier", "Femme", 1957), ("Adama", "Coulibaly", "Homme", 1986),
]

#: (nom de l'activité, jour de la semaine 0=lundi, début, fin)
ACTIVITES = [
    ("Atelier numérique (exemple)", 1, "14:00", "16:00"),
    ("Café des parents (exemple)", 3, "09:30", "11:30"),
    ("Cours de français (exemple)", 4, "10:00", "12:00"),
]

NB_SEMAINES = 6
MONTANT_ADHESION = 10.0


def _quartiers_de_la_ville(ville: str | None) -> list:
    from app.models import Quartier
    from app.services.villes import memes_villes

    if not ville:
        return []
    return [q for q in Quartier.query.order_by(Quartier.id).all() if memes_villes(q.ville, ville)]


def creer(auteur_id: int | None = None, aujourd_hui: date | None = None) -> dict:
    """Crée le jeu d'exemple dans la transaction en cours (sans la valider)."""
    from app.models import AtelierActivite, Cotisation, Participant, PresenceActivite, SessionActivite
    from app.secteurs import get_secteur_labels
    from app.services import encaissements
    from app.services.cotisations import annee_scolaire_de
    from app.services.territoire import territoire

    aujourd_hui = aujourd_hui or date.today()
    ville = territoire().ville_siege
    quartiers = _quartiers_de_la_ville(ville)
    secteurs = get_secteur_labels(active_only=True) or ["Numérique"]

    habitants = []
    for rang, (prenom, nom, genre, annee) in enumerate(HABITANTS):
        habitant = Participant(
            prenom=prenom, nom=nom, genre=genre, date_naissance=date(annee, 1 + rang % 12, 1 + rang % 27),
            ville=ville, quartier_id=quartiers[rang % len(quartiers)].id if quartiers else None,
            created_secteur=secteurs[rang % len(secteurs)],
        )
        db.session.add(habitant)
        habitants.append(habitant)
    db.session.flush()

    nb_seances = nb_presences = 0
    for rang, (nom_activite, jour, debut, fin) in enumerate(ACTIVITES):
        secteur = secteurs[rang % len(secteurs)]
        activite = AtelierActivite(nom=nom_activite, secteur=secteur, type_atelier="COLLECTIF",
                                   capacite_defaut=12, is_active=True)
        db.session.add(activite)
        db.session.flush()
        # Les habitués : dix habitants, différents d'une activité à l'autre.
        habitues = [habitants[(rang * 7 + i) % len(habitants)] for i in range(10)]
        dernier = aujourd_hui - timedelta(days=(aujourd_hui.weekday() - jour) % 7 or 7)
        for semaine in range(NB_SEMAINES):
            seance = SessionActivite(
                atelier_id=activite.id, secteur=secteur, session_type="COLLECTIF",
                date_session=dernier - timedelta(weeks=semaine), heure_debut=debut, heure_fin=fin,
                capacite=12, statut="realisee",
            )
            db.session.add(seance)
            db.session.flush()
            nb_seances += 1
            for i, habitant in enumerate(habitues):
                if (i * 3 + semaine * 5 + rang) % 10 < 7:  # environ 7 sur 10, toujours les mêmes
                    db.session.add(PresenceActivite(session_id=seance.id, participant_id=habitant.id))
                    nb_presences += 1
    db.session.flush()

    # Adhésions de l'année : huit habitants, six réglés (espèces ou chèque), deux à relancer.
    annee = annee_scolaire_de(aujourd_hui)
    nb_reglees = 0
    for rang, habitant in enumerate(habitants[:8]):
        adhesion = Cotisation(annee_scolaire=annee, type_cotisation="adhesion_individuelle",
                              participant_id=habitant.id, montant_du=MONTANT_ADHESION,
                              date_reference=aujourd_hui - timedelta(days=30 - rang),
                              created_by_user_id=auteur_id)
        db.session.add(adhesion)
        db.session.flush()
        if rang < 6:
            encaissement = encaissements.enregistrer(
                MONTANT_ADHESION, "especes" if rang % 2 else "cheque",
                date_encaissement=adhesion.date_reference, participant_id=habitant.id,
                libelle="Adhésion (exemple)", user_id=auteur_id)
            encaissements.ventiler(encaissement, [adhesion], user_id=auteur_id)
            nb_reglees += 1
    db.session.flush()

    return {
        "habitants": len(habitants), "activites": len(ACTIVITES), "seances": nb_seances,
        "presences": nb_presences, "adhesions": 8, "adhesions_reglees": nb_reglees,
    }
