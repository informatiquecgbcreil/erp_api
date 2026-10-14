"""Le parachute des imports Excel : sauvegarde avant, annulation après.

Un import mal préparé (colonnes inversées, mauvais onglet, mauvais secteur)
crée des dizaines de fiches et des centaines de présences en un clic. La
seule issue était de restaurer une sauvegarde, ce qui efface aussi tout ce
que l'équipe a saisi depuis.

Désormais, pour l'import des présences et celui de l'annuaire :

1. une **sauvegarde** complète est faite juste avant l'import réel ; si elle
   échoue, l'import n'a pas lieu ;
2. l'import tient un **journal** de ce qu'il crée (fiches, présences,
   séances, activités, quartiers) et de ce qu'il modifie sur des fiches
   existantes (valeur d'avant, valeur posée) ;
3. le **dernier** import de chaque écran peut être **annulé** : on retire
   exactement ce qu'il a créé et on remet ce qu'il a modifié — sauf ce qui a
   servi depuis (une fiche qui a reçu d'autres présences, une séance où
   quelqu'un a émargé, une valeur retouchée à la main), qui est conservé et
   compté dans le compte rendu.

Le compte rendu et le journal d'audit ne contiennent que des nombres : aucun
nom de participant (audit 3.x, données personnelles).
"""
from __future__ import annotations

import json
from datetime import date

from app.extensions import db
from app.models import (
    AtelierActivite,
    ImportLot,
    OrientationAccesDroit,
    Participant,
    PresenceActivite,
    Quartier,
    SessionActivite,
)
from app.utils.dates import utcnow

#: Les deux écrans d'import concernés, et leur libellé.
NATURES = {
    "presences_excel": "Import Excel des présences",
    "annuaire": "Import de l'annuaire",
}

#: Ce que l'import crée, dans l'ordre où l'annulation le retire.
GENRES_CREES = ("presences", "sessions", "ateliers", "participants", "quartiers")

#: Champs d'une fiche existante que l'import des présences peut compléter.
CHAMPS_MODIFIABLES = ("ville", "quartier_id", "date_naissance", "genre")


class SauvegardeImpossible(RuntimeError):
    """La sauvegarde d'avant import a échoué : l'import ne doit pas avoir lieu."""


def _serialiser(valeur):
    return valeur.isoformat() if isinstance(valeur, date) else valeur


class Journal:
    """Ce qu'un import crée et modifie, au fil de l'eau."""

    def __init__(self):
        self.crees: dict[str, list[int]] = {genre: [] for genre in GENRES_CREES}
        self.modifications: list[dict] = []

    def cree(self, genre: str, identifiant: int | None) -> None:
        if identifiant is not None and identifiant not in self.crees[genre]:
            self.crees[genre].append(int(identifiant))

    def modifie(self, participant_id: int, champ: str, avant, apres) -> None:
        avant, apres = _serialiser(avant), _serialiser(apres)
        if avant != apres:
            self.modifications.append(
                {"participant_id": int(participant_id), "champ": champ, "avant": avant, "apres": apres})

    @property
    def vide(self) -> bool:
        """Rien créé ni modifié (un fichier rejoué) : pas de lot à retenir,
        sinon il deviendrait « le dernier » et l'import précédent ne
        pourrait plus être annulé."""
        return not self.modifications and not any(self.crees.values())

    def en_json(self) -> str:
        return json.dumps({**self.crees, "modifications": self.modifications})


def sauvegarder_avant_import() -> str:
    """Fait la sauvegarde complète d'avant import ; renvoie le nom du lot."""
    from app.services.sauvegarde import creer_sauvegarde

    try:
        return creer_sauvegarde()["base"]
    except Exception as exc:  # noqa: BLE001 — tout échec doit bloquer l'import
        raise SauvegardeImpossible(str(exc) or exc.__class__.__name__) from exc


def enregistrer(nature: str, fichier: str | None, journal: Journal, sauvegarde: str | None,
                auteur_id: int | None) -> ImportLot:
    """Ajoute le lot à la session, sans valider : il part avec l'import, ou pas du tout."""
    lot = ImportLot(nature=nature, fichier=(fichier or "")[:255] or None, sauvegarde=sauvegarde,
                    cree_par_id=auteur_id, elements_json=journal.en_json())
    db.session.add(lot)
    return lot


def elements(lot: ImportLot) -> dict:
    try:
        contenu = json.loads(lot.elements_json or "{}")
    except ValueError:
        contenu = {}
    return {**{genre: contenu.get(genre) or [] for genre in GENRES_CREES},
            "modifications": contenu.get("modifications") or []}


def compte_rendu(lot: ImportLot) -> dict | None:
    if not lot.annulation_json:
        return None
    try:
        return json.loads(lot.annulation_json)
    except ValueError:
        return None


#: Libellés du compte rendu, au pluriel.
LIBELLES_RETIRES = {
    "participants": "fiche(s)",
    "presences": "présence(s)",
    "sessions": "séance(s) mises à la corbeille",
    "ateliers": "activité(s) mises à la corbeille",
    "quartiers": "quartier(s)",
    "modifications": "valeur(s) remises comme avant",
}


def message_annulation(rapport: dict) -> str:
    """Le compte rendu en une phrase, sans aucun nom."""
    retires = [f"{n} {LIBELLES_RETIRES[genre]}" for genre, n in rapport["retires"].items() if n]
    texte = "Import annulé : " + (", ".join(retires) if retires else "il n'y avait plus rien à retirer") + "."
    if rapport["conserves"]:
        gardes = ", ".join(f"{n} {raison}" for raison, n in rapport["conserves"].items())
        texte += f" Conservé car utilisé depuis : {gardes}."
    return texte


def dernier_lot(nature: str) -> ImportLot | None:
    """Le dernier import de cet écran, annulé ou non."""
    return ImportLot.query.filter_by(nature=nature).order_by(ImportLot.id.desc()).first()


def annulable(lot: ImportLot | None) -> bool:
    """Seul le dernier import d'un écran s'annule, et une seule fois : un
    import plus récent a pu s'appuyer sur ce que le précédent avait créé."""
    if lot is None or lot.annule_le is not None:
        return False
    dernier = dernier_lot(lot.nature)
    return dernier is not None and dernier.id == lot.id


def peut_annuler(lot: ImportLot | None, utilisateur) -> bool:
    """L'auteur de l'import, ou un compte qui voit toute la structure : un
    responsable de secteur importe dans son secteur, il n'annule pas
    l'import d'un autre."""
    if not annulable(lot) or utilisateur is None or not getattr(utilisateur, "is_authenticated", False):
        return False
    from app.rbac import can

    return lot.cree_par_id == utilisateur.id or bool(can("scope:all_secteurs"))


def _valeur(participant: Participant, champ: str):
    return _serialiser(getattr(participant, champ))


def _remettre(participant: Participant, champ: str, valeur) -> None:
    if champ == "date_naissance" and valeur:
        valeur = date.fromisoformat(valeur)
    setattr(participant, champ, valeur)


def annuler(lot: ImportLot, auteur_id: int | None) -> dict:
    """Retire ce que l'import a créé, remet ce qu'il a modifié, et valide.

    Lève ``ValueError`` si le lot n'est pas (ou plus) annulable.
    Renvoie le compte rendu : {"retires": {...}, "conserves": {...}}.
    """
    if not annulable(lot):
        raise ValueError("Seul le dernier import peut être annulé, et une seule fois.")

    from app.services.participant_suppression import analyser, supprimer_definitivement

    contenu = elements(lot)
    retires = {genre: 0 for genre in (*GENRES_CREES, "modifications")}
    conserves: dict[str, int] = {}

    def conserver(raison: str) -> None:
        conserves[raison] = conserves.get(raison, 0) + 1

    # 1) Les valeurs posées sur des fiches existantes, si personne n'y a touché.
    for modif in contenu["modifications"]:
        participant = db.session.get(Participant, modif["participant_id"])
        if participant is None:
            continue
        if _valeur(participant, modif["champ"]) == modif["apres"]:
            _remettre(participant, modif["champ"], modif["avant"])
            retires["modifications"] += 1
        else:
            conserver("valeurs retouchées à la main depuis l'import")

    # 2) Les présences créées. Une présence signée depuis reste.
    for presence_id in contenu["presences"]:
        presence = db.session.get(PresenceActivite, presence_id)
        if presence is None:
            continue
        if presence.signature_path:
            conserver("présences signées depuis l'import")
            continue
        db.session.delete(presence)
        retires["presences"] += 1
    db.session.flush()

    # 3) Les séances et activités créées : à la corbeille, comme à la main,
    # seulement si plus rien ne s'y rattache.
    maintenant = utcnow()
    for session_id in contenu["sessions"]:
        seance = db.session.get(SessionActivite, session_id)
        if seance is None or seance.is_deleted:
            continue
        if PresenceActivite.query.filter_by(session_id=seance.id).count():
            conserver("séances où d'autres présences ont été saisies")
            continue
        seance.is_deleted, seance.deleted_at = True, maintenant
        seance.kiosk_open, seance.kiosk_pin, seance.kiosk_token = False, None, None
        retires["sessions"] += 1
    db.session.flush()

    for atelier_id in contenu["ateliers"]:
        atelier = db.session.get(AtelierActivite, atelier_id)
        if atelier is None or atelier.is_deleted:
            continue
        if SessionActivite.query.filter_by(atelier_id=atelier.id, is_deleted=False).count():
            conserver("activités qui ont reçu d'autres séances")
            continue
        atelier.is_deleted, atelier.deleted_at = True, maintenant
        retires["ateliers"] += 1

    # 4) Les fiches créées, si elles n'ont servi à rien d'autre depuis.
    for participant_id in contenu["participants"]:
        participant = db.session.get(Participant, participant_id)
        if participant is None:
            continue
        if analyser(participant)["total"]:
            conserver("fiches utilisées depuis l'import (présences, inscriptions…)")
            continue
        supprimer_definitivement(participant)
        retires["participants"] += 1
    db.session.flush()

    # 5) Les quartiers créés, s'ils n'ont plus d'habitant ni d'orientation.
    for quartier_id in contenu["quartiers"]:
        quartier = db.session.get(Quartier, quartier_id)
        if quartier is None:
            continue
        if (Participant.query.filter_by(quartier_id=quartier.id).count()
                or OrientationAccesDroit.query.filter_by(quartier_id=quartier.id).count()):
            conserver("quartiers où vivent d'autres habitants")
            continue
        db.session.delete(quartier)
        retires["quartiers"] += 1

    rapport = {"retires": retires, "conserves": conserves}
    lot.annule_le = maintenant
    lot.annule_par_id = auteur_id
    lot.annulation_json = json.dumps(rapport, ensure_ascii=False)
    db.session.commit()

    from app.services.audit import journaliser

    journaliser("import.annule", cible=f"import_lot:{lot.id}",
                details={"nature": lot.nature, **rapport})
    return rapport
