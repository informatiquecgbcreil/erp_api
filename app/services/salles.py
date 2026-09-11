"""Moteur des salles : arbre des espaces, disponibilité, occupations.

Tout le module repose sur UNE règle, écrite ici et nulle part ailleurs :

    Occuper un espace rend indisponibles cet espace, TOUS SES ANCÊTRES et
    TOUS SES DESCENDANTS.

C'est elle qui règle sans le moindre cas particulier :

- la grande salle d'activité séparable par cloisons amovibles : réserver
  « Demi-salle A » interdit « Grande salle » (ancêtre) mais laisse
  « Demi-salle B » libre ; réserver « Grande salle » interdit les deux
  moitiés (descendants) ;
- l'espace petite enfance : le louer en entier bloque le dortoir, et
  réserver le dortoir seul bloque la location de l'aile complète.

Personne, ni la secrétaire d'accueil ni l'animateur qui programme son
atelier, ne doit jamais avoir à faire ce raisonnement de tête.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date

from app.extensions import db
from app.models import (
    AgendaCreneau,
    Espace,
    Occupation,
    SessionActivite,
    Site,
    minutes_depuis_texte,
    texte_depuis_minutes,
)


class SalleErreur(Exception):
    """Erreur métier destinée à être affichée telle quelle à l'utilisateur."""


# ---------------------------------------------------------------------------
# Horaires
# ---------------------------------------------------------------------------

def normaliser_plage(debut, fin) -> tuple[int, int]:
    """Valide et convertit un couple d'horaires en minutes depuis minuit.

    Accepte indifféremment « 14:00 », « 9h30 », « 0900 » ou des entiers,
    parce que les séances existantes stockent leurs heures en texte libre.
    """
    m_debut = minutes_depuis_texte(debut)
    m_fin = minutes_depuis_texte(fin)
    if m_debut is None or m_fin is None:
        raise SalleErreur("Il faut une heure de début et une heure de fin valides (par exemple 14:00).")
    if m_fin <= m_debut:
        raise SalleErreur(
            f"L'heure de fin ({texte_depuis_minutes(m_fin)}) doit être après "
            f"l'heure de début ({texte_depuis_minutes(m_debut)})."
        )
    return m_debut, m_fin


# ---------------------------------------------------------------------------
# Arbre des espaces
# ---------------------------------------------------------------------------

def _index_site(site_id: int) -> tuple[dict[int, Espace], dict[int, list[int]]]:
    """Charge tous les espaces d'un site en une requête.

    Un site compte quelques dizaines d'espaces : les charger d'un bloc et
    parcourir l'arbre en mémoire est plus simple, plus rapide et surtout
    identique sur SQLite et PostgreSQL — contrairement aux requêtes
    récursives, dont le comportement diffère entre les deux.

    Le résultat est mémorisé le temps de la requête HTTP : l'écran de
    disponibilité teste une trentaine de salles d'affilée et lirait sinon
    le même arbre trente fois.
    """
    cache = None
    try:  # hors contexte de requête (tests, tâches), on s'en passe
        from flask import g, has_request_context, request

        # Lectures seulement : pendant un POST l'arbre est en train d'être
        # modifié, un cache y servirait une version périmée.
        if has_request_context() and request.method in ("GET", "HEAD"):
            cache = getattr(g, "_salles_index", None)
            if cache is None:
                cache = g._salles_index = {}
            if site_id in cache:
                return cache[site_id]
    except Exception:  # noqa: BLE001 - le cache n'est qu'un confort
        cache = None

    espaces = Espace.query.filter(Espace.site_id == site_id).all()
    par_id = {e.id: e for e in espaces}
    enfants: dict[int, list[int]] = defaultdict(list)
    for e in espaces:
        if e.parent_id is not None and e.parent_id in par_id:
            enfants[e.parent_id].append(e.id)

    resultat = (par_id, enfants)
    if cache is not None:
        cache[site_id] = resultat
    return resultat


def zone_conflit_ids(espace: Espace) -> set[int]:
    """Les identifiants d'espaces rendus indisponibles par l'occupation de
    ``espace`` : lui-même, ses ancêtres et ses descendants.

    Les boucles éventuelles (parent mal saisi) sont neutralisées : on ne
    repasse jamais deux fois sur le même nœud.
    """
    if espace is None or espace.id is None:
        return set()
    par_id, enfants = _index_site(espace.site_id)
    ids = {espace.id}

    # Ancêtres : on remonte les parents.
    courant = par_id.get(espace.id)
    while courant is not None and courant.parent_id is not None:
        if courant.parent_id in ids:
            break  # cycle
        ids.add(courant.parent_id)
        courant = par_id.get(courant.parent_id)

    # Descendants : parcours en profondeur du sous-arbre.
    pile = list(enfants.get(espace.id, []))
    while pile:
        courant_id = pile.pop()
        if courant_id in ids:
            continue
        ids.add(courant_id)
        pile.extend(enfants.get(courant_id, []))

    return ids


def arbre_du_site(site_id: int) -> list[tuple[Espace, int]]:
    """L'arbre aplati pour l'affichage : ``(espace, profondeur)`` dans
    l'ordre de lecture, en respectant ``ordre`` puis le nom."""
    par_id, enfants = _index_site(site_id)
    resultat: list[tuple[Espace, int]] = []

    def cle(espace_id: int):
        e = par_id[espace_id]
        return (e.ordre or 0, (e.nom or "").lower())

    def descendre(espace_id: int, profondeur: int) -> None:
        resultat.append((par_id[espace_id], profondeur))
        for enfant_id in sorted(enfants.get(espace_id, []), key=cle):
            descendre(enfant_id, profondeur + 1)

    racines = sorted([e.id for e in par_id.values() if e.parent_id not in par_id], key=cle)
    for racine_id in racines:
        descendre(racine_id, 0)
    return resultat


def espaces_reservables(actifs_seulement: bool = True):
    """Les espaces qui peuvent recevoir une occupation (planning)."""
    q = Espace.query.filter(Espace.reservable.is_(True))
    if actifs_seulement:
        q = q.filter(Espace.actif.is_(True))
    return q.order_by(Espace.site_id, Espace.ordre, Espace.nom).all()


def espaces_louables(actifs_seulement: bool = True):
    """Les espaces mobilisables par un tiers (mise à disposition, location)."""
    q = Espace.query.filter(Espace.louable.is_(True), Espace.reservable.is_(True))
    if actifs_seulement:
        q = q.filter(Espace.actif.is_(True))
    return q.order_by(Espace.site_id, Espace.ordre, Espace.nom).all()


def espaces_stockage(actifs_seulement: bool = True):
    """Les emplacements proposables dans l'inventaire (salles ET armoires)."""
    q = Espace.query.filter(Espace.stockage.is_(True))
    if actifs_seulement:
        q = q.filter(Espace.actif.is_(True))
    return q.order_by(Espace.site_id, Espace.ordre, Espace.nom).all()


# ---------------------------------------------------------------------------
# Disponibilité
# ---------------------------------------------------------------------------

def _battement(espace_a: Espace, espace_b: Espace) -> int:
    """Le battement exigé entre deux occupations : le plus contraignant des
    deux espaces l'emporte.

    Un seul nombre par espace plutôt qu'un « avant » et un « après » : sinon
    deux réservations successives cumuleraient les deux marges et il faudrait
    une heure de battement là où trente minutes suffisent.
    """
    return max(int(espace_a.battement_minutes or 0), int(espace_b.battement_minutes or 0))


def conflits(
    espace: Espace,
    date_jour: Date,
    debut,
    fin,
    *,
    exclure_occupation_id: int | None = None,
    inclure_options: bool = True,
) -> list[Occupation]:
    """Les occupations qui empêchent de retenir ``espace`` sur cette plage.

    Deux occupations se gênent si leurs plages se chevauchent, ou si
    l'intervalle qui les sépare est plus court que le battement exigé par
    la salle (remise en état, ménage, aération).

    ``exclure_occupation_id`` sert lors d'une modification : une occupation
    n'entre évidemment pas en conflit avec elle-même.
    """
    m_debut, m_fin = normaliser_plage(debut, fin)
    ids_zone = zone_conflit_ids(espace)
    if not ids_zone:
        return []

    statuts_bloquants = ["confirme"] + (["option"] if inclure_options else [])

    # Fenêtre large côté base (le battement maximal plausible reste petit),
    # puis filtrage fin en Python où l'on connaît le battement de chaque
    # espace concerné.
    marge = 240
    candidates = (
        Occupation.query
        .filter(
            Occupation.espace_id.in_(ids_zone),
            Occupation.date_jour == date_jour,
            Occupation.statut.in_(statuts_bloquants),
            Occupation.minute_debut < m_fin + marge,
            Occupation.minute_fin > m_debut - marge,
        )
        .order_by(Occupation.minute_debut)
        .all()
    )

    trouves = []
    for occ in candidates:
        if exclure_occupation_id is not None and occ.id == exclure_occupation_id:
            continue
        gap = _battement(espace, occ.espace)
        if m_debut < (occ.minute_fin + gap) and occ.minute_debut < (m_fin + gap):
            trouves.append(occ)
    return trouves


def est_disponible(espace: Espace, date_jour: Date, debut, fin, **kwargs) -> bool:
    """Vrai si la plage est libre pour cet espace."""
    return not conflits(espace, date_jour, debut, fin, **kwargs)


def message_conflit(occ: Occupation, espace_demande: Espace) -> str:
    """Phrase lisible expliquant POURQUOI c'est indisponible.

    La secrétaire doit comprendre en une ligne qu'elle bute sur la salle
    du dessus ou du dessous, pas seulement qu'« il y a un conflit ».
    """
    quoi = occ.titre or occ.origine_label
    if occ.espace_id == espace_demande.id:
        ou = "déjà occupée"
    elif occ.espace_id in {a.id for a in espace_demande.ancetres()}:
        ou = f"incluse dans « {occ.espace.nom} », qui est occupée"
    else:
        ou = f"partiellement occupée : « {occ.espace.nom} » est pris"
    return f"{espace_demande.nom} est {ou} le {occ.date_jour.strftime('%d/%m/%Y')} de {occ.plage} ({quoi})."


def recherche_disponibilite(
    date_jour: Date,
    debut,
    fin,
    *,
    effectif: int | None = None,
    louables_seulement: bool = False,
    site_id: int | None = None,
) -> list[dict]:
    """L'écran que la secrétaire utilisera le plus : « qui est libre ce
    jour-là, sur ce créneau, pour tant de personnes ? »

    Retourne une ligne par espace avec son verdict et, quand c'est occupé,
    la raison en clair.
    """
    normaliser_plage(debut, fin)  # valide tôt, message clair
    candidats = espaces_louables() if louables_seulement else espaces_reservables()
    if site_id:
        candidats = [e for e in candidats if e.site_id == site_id]

    lignes = []
    for espace in candidats:
        genants = conflits(espace, date_jour, debut, fin)
        capacite = espace.capacite_affichee
        trop_petit = bool(effectif and capacite and effectif > capacite)
        hors_norme = bool(
            effectif and espace.capacite_reglementaire and effectif > espace.capacite_reglementaire
        )
        lignes.append({
            "espace": espace,
            "libre": not genants,
            "conflits": genants,
            "raison": message_conflit(genants[0], espace) if genants else None,
            "capacite_insuffisante": trop_petit,
            "depassement_reglementaire": hors_norme,
        })

    # Les salles libres d'abord, puis les plus petites capables d'accueillir
    # l'effectif : on ne propose pas la grande salle pour six personnes.
    lignes.sort(key=lambda l: (
        not l["libre"],
        l["capacite_insuffisante"],
        l["espace"].capacite_affichee or 9999,
        l["espace"].nom.lower(),
    ))
    return lignes


# ---------------------------------------------------------------------------
# Blocage automatique : séances et créneaux réservent leur salle tout seuls
# ---------------------------------------------------------------------------
#
# Sept endroits différents créent des séances dans l'application (saisie
# manuelle, grille hebdomadaire, import Excel, reprise historique…). Appeler
# une fonction de synchronisation dans chacun, c'est se garantir un oubli au
# huitième. On branche donc UN SEUL point d'écoute sur la session SQLAlchemy :
# tout ce qui entre en base passe devant, y compris les chemins qui n'existent
# pas encore.

def _plage_seance(seance: SessionActivite) -> tuple[Date | None, int | None, int | None]:
    """Extrait (jour, début, fin) d'une séance, collective ou individuelle.

    Les séances individuelles utilisent ``rdv_*`` là où les collectives
    utilisent ``date_session`` / ``heure_*``. Quand la fin manque mais que la
    durée est connue, on la reconstitue.
    """
    jour = seance.date_session or seance.rdv_date
    debut = minutes_depuis_texte(seance.heure_debut) or minutes_depuis_texte(seance.rdv_debut)
    fin = minutes_depuis_texte(seance.heure_fin) or minutes_depuis_texte(seance.rdv_fin)
    if debut is not None and fin is None and seance.duree_minutes:
        fin = debut + int(seance.duree_minutes)
    return jour, debut, fin


def _titre_seance(seance: SessionActivite) -> str:
    """Nom de l'atelier, recopié dans l'occupation pour l'affichage.

    Sur une séance qui vient d'être ajoutée, seule ``atelier_id`` est posée :
    la relation ``seance.atelier`` ne sera résolue qu'après le flush, et
    renverrait ``None`` à ce stade. On va donc chercher le nom directement,
    sans déclencher de flush au milieu du flush en cours.
    """
    from app.models import AtelierActivite

    atelier = getattr(seance, "atelier", None)
    if atelier is None and seance.atelier_id:
        with db.session.no_autoflush:
            atelier = db.session.get(AtelierActivite, seance.atelier_id)
    nom = (getattr(atelier, "nom", None) or "Séance").strip()
    return nom[:200]


def _seance_doit_occuper(seance: SessionActivite) -> bool:
    """Une séance ne bloque une salle que si elle a lieu pour de bon."""
    if not seance.espace_id or getattr(seance, "is_deleted", False):
        return False
    if (seance.statut or "").lower() == "annulee":
        return False
    jour, debut, fin = _plage_seance(seance)
    return bool(jour and debut is not None and fin is not None and fin > debut)


def _creneau_doit_occuper(creneau: AgendaCreneau) -> bool:
    if not creneau.espace_id or not creneau.date_creneau:
        return False
    debut = minutes_depuis_texte(creneau.heure_debut)
    fin = minutes_depuis_texte(creneau.heure_fin)
    return bool(debut is not None and fin is not None and fin > debut)


def _appliquer(session_db, source, *, champ: str, doit_occuper: bool, valeurs: dict) -> None:
    """Crée, met à jour ou supprime l'occupation miroir d'une source.

    Le rattachement se fait par la RELATION (``occupation.session = seance``)
    et non par l'identifiant : une séance qui vient d'être créée n'a pas
    encore d'identifiant à ce stade, SQLAlchemy résout la clé au moment du
    flush.
    """
    with session_db.no_autoflush:
        existantes = [o for o in (source.occupations or []) if o not in session_db.deleted]

    if not doit_occuper:
        for occ in existantes:
            session_db.delete(occ)
        return

    occupation = existantes[0] if existantes else None
    for surplus in existantes[1:]:  # ceinture et bretelles : jamais de doublon
        session_db.delete(surplus)

    if occupation is None:
        occupation = Occupation()
        setattr(occupation, champ, source)
        session_db.add(occupation)

    for attribut, valeur in valeurs.items():
        if getattr(occupation, attribut, None) != valeur:
            setattr(occupation, attribut, valeur)


def synchroniser_seance(session_db, seance: SessionActivite) -> None:
    """Aligne l'occupation d'une séance sur son état courant."""
    jour, debut, fin = _plage_seance(seance)
    _appliquer(
        session_db, seance,
        champ="session",
        doit_occuper=_seance_doit_occuper(seance),
        valeurs={
            "espace_id": seance.espace_id,
            "date_jour": jour,
            "minute_debut": debut,
            "minute_fin": fin,
            "origine": "seance",
            "statut": "confirme",
            "titre": _titre_seance(seance),
            "secteur": seance.secteur,
            "effectif_prevu": seance.capacite,
        },
    )


def synchroniser_creneau(session_db, creneau: AgendaCreneau) -> None:
    """Aligne l'occupation d'un créneau d'agenda sur son état courant."""
    _appliquer(
        session_db, creneau,
        champ="creneau",
        doit_occuper=_creneau_doit_occuper(creneau),
        valeurs={
            "espace_id": creneau.espace_id,
            "date_jour": creneau.date_creneau,
            "minute_debut": minutes_depuis_texte(creneau.heure_debut),
            "minute_fin": minutes_depuis_texte(creneau.heure_fin),
            "origine": "creneau",
            "statut": "confirme",
            "titre": (creneau.titre or "Créneau")[:200],
            "secteur": None,
        },
    )


def enregistrer_synchronisation_auto() -> None:
    """Branche le point d'écoute unique sur la session SQLAlchemy.

    Appelé une fois depuis la fabrique d'application. ``before_flush`` est le
    bon moment : c'est la dernière étape où l'on peut encore ajouter ou
    supprimer des objets dans la transaction en cours, donc l'occupation
    part en base dans le même mouvement que la séance qui la justifie —
    jamais l'une sans l'autre.
    """
    from sqlalchemy import event

    if getattr(enregistrer_synchronisation_auto, "_fait", False):
        return

    @event.listens_for(db.session, "before_flush")
    def _synchroniser(session_db, flush_context, instances):  # pragma: no cover - via tests d'intégration
        for objet in list(session_db.new) + list(session_db.dirty):
            if objet in session_db.deleted:
                continue
            if isinstance(objet, SessionActivite):
                synchroniser_seance(session_db, objet)
            elif isinstance(objet, AgendaCreneau):
                synchroniser_creneau(session_db, objet)

    enregistrer_synchronisation_auto._fait = True


def reconcilier_occupations() -> dict[str, int]:
    """Reconstruit toutes les occupations issues des séances et des créneaux.

    Filet de sécurité : à lancer après un import en masse, une reprise de
    données ou le moindre doute. L'opération est idempotente — la relancer
    deux fois de suite donne exactement le même résultat.
    """
    compteurs = {"seances": 0, "creneaux": 0, "orphelines": 0}

    for seance in SessionActivite.query.filter(SessionActivite.espace_id.isnot(None)).all():
        synchroniser_seance(db.session, seance)
        compteurs["seances"] += 1
    for creneau in AgendaCreneau.query.filter(AgendaCreneau.espace_id.isnot(None)).all():
        synchroniser_creneau(db.session, creneau)
        compteurs["creneaux"] += 1

    # Occupations dont la source a perdu sa salle entre-temps.
    orphelines = (
        Occupation.query
        .filter(Occupation.origine.in_(["seance", "creneau"]))
        .filter(Occupation.session_id.is_(None), Occupation.creneau_id.is_(None))
        .all()
    )
    for occ in orphelines:
        db.session.delete(occ)
        compteurs["orphelines"] += 1

    db.session.commit()
    return compteurs
