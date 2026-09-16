"""Répartir la participation entre les secteurs, au prorata des venues.

## Le problème

Deux cotisations, deux natures très différentes :

- l'**adhésion** couvre l'assurance. C'est la part légale, elle ne se
  découpe pas ;
- la **participation** finance les secteurs. C'est elle qu'on répartit.

Jusqu'ici, on demandait à l'accueil « quel secteur vous fait venir ? » et
la participation entière était attribuée à ce secteur-là. Simple, mais
faux dès que la personne circule : quelqu'un qui vient trente fois dans
l'année, dont vingt en Numérique et dix ailleurs, faisait pourtant vivre
six secteurs avec une participation qui n'en créditait qu'un.

D'où le prorata : **chaque venue pèse une part égale**, et la
participation se découpe selon là où la personne est réellement allée.

## Ce qui rend ce calcul possible

``SessionActivite.secteur`` est stocké **séance par séance**, et pas au
niveau de l'atelier. Le modèle le dit lui-même : « animer une séance pour
un autre secteur : elle compte alors pour ce secteur-là, pas pour le
sien ». Autrement dit, la question difficile — à qui profite une séance
d'un atelier intersecteur — est déjà tranchée à la saisie. Ici, on compte.

## Les règles, et pourquoi

**Seules les venues réelles comptent** (``present`` et ``retard``). Une
absence excusée documente le fait que la personne N'EST PAS venue : le
secteur ne l'a pas accueillie ce jour-là, il ne touche pas la part. C'est
aussi le calcul le plus facile à défendre devant un financeur.

**Zéro venue sur l'année : tout va au secteur qui a fait venir la
personne.** Elle a bien payé, l'argent doit atterrir quelque part, et
c'est ce secteur qui a fait le travail d'accueil et d'inscription. On
retombe alors exactement sur l'ancienne règle — le prorata ne fait jamais
disparaître d'argent des vues.

**Les séances annulées ou supprimées ne comptent pas.** Une séance qui
n'a pas eu lieu n'a accueilli personne.

**La somme des parts fait exactement le montant réparti.** Pas
« à peu près » : au centime. Un tableau dont les colonnes ne totalisent
pas le montant encaissé est un tableau que la comptabilité renvoie, et
elle a raison. Voir ``decouper``.

## Deux montants, et ils ne servent pas à la même chose

- le **dû** : ce qui a été facturé. C'est le budget théorique ;
- l'**encaissé** : ce qui est réellement rentré. C'est ce qu'un référent
  peut engager.

Les deux sont calculés et affichés. En cas de doute, c'est l'encaissé qui
fait foi — on n'engage pas de l'argent qui n'est pas arrivé.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import (
    Cotisation,
    Paiement,
    Participant,
    PresenceActivite,
    SessionActivite,
)

#: Les statuts de présence qui valent « la personne est venue ».
#: Une absence excusée n'en fait pas partie : elle atteste le contraire.
VENUES_REELLES = ("present", "retard")

#: Étiquette des personnes dont aucune venue n'a été trouvée sur l'année.
SANS_SECTEUR = "Sans secteur"


# ---------------------------------------------------------------------------
# L'arrondi : la partie qui doit être irréprochable
# ---------------------------------------------------------------------------

def decouper(montant: float, poids: dict[str, int]) -> dict[str, float]:
    """Découpe ``montant`` selon ``poids``, en bouclant au centime près.

    L'arrondi naïf (arrondir chaque part séparément) ne tombe presque
    jamais juste : 10 € en trois parts égales donne 3,33 × 3 = 9,99 €, et
    il manque un centime que personne ne sait expliquer. Sur une centaine
    de participations, l'écart devient visible et décrédibilise tout le
    tableau.

    On applique donc la **méthode du plus fort reste** : chaque part est
    d'abord tronquée au centime inférieur, puis les centimes restants sont
    donnés un par un aux parts dont la décimale sacrifiée était la plus
    grande. La somme est alors exacte par construction.

    En cas d'égalité parfaite des restes, on départage par le nom du
    secteur : le calcul doit rendre le même résultat à chaque exécution,
    sinon deux exports du même jour ne se ressemblent pas.
    """
    total_poids = sum(max(0, int(p)) for p in poids.values())
    if total_poids <= 0 or not poids:
        return {}

    centimes_a_repartir = int(round(float(montant or 0) * 100))
    if centimes_a_repartir == 0:
        return {cle: 0.0 for cle in poids}

    parts: dict[str, int] = {}
    restes: list[tuple[int, str]] = []
    for cle, p in poids.items():
        exact = centimes_a_repartir * max(0, int(p))
        entier, reste = divmod(exact, total_poids)
        parts[cle] = entier
        restes.append((reste, cle))

    # Les centimes non attribués par la troncature, aux plus forts restes.
    manquants = centimes_a_repartir - sum(parts.values())
    restes.sort(key=lambda item: (-item[0], item[1]))
    for _, cle in restes[:manquants]:
        parts[cle] += 1

    return {cle: round(centimes / 100, 2) for cle, centimes in parts.items()}


# ---------------------------------------------------------------------------
# Les venues
# ---------------------------------------------------------------------------

def bornes_annee_scolaire(annee: int) -> tuple[date, date]:
    """Du 1er septembre au 31 août — la convention de toute l'application."""
    return date(annee, 9, 1), date(annee + 1, 8, 31)


def venues_par_secteur(annee: int, participant_ids=None,
                       a_la_date: date | None = None) -> dict[int, dict[str, int]]:
    """``{participant_id: {secteur: nombre de venues}}`` pour l'année.

    ``a_la_date`` arrête le comptage à cette date incluse. C'est ce qui
    donne son sens à un arrêté : « au 31 décembre » doit compter les
    venues jusqu'au 31 décembre, et pas celles de février qu'on ne
    connaissait pas encore.

    Une seule requête, groupée en base : compter les venues de trois cents
    personnes ne doit pas coûter trois cents allers-retours.
    """
    debut, fin = bornes_annee_scolaire(annee)
    if a_la_date is not None and a_la_date < fin:
        fin = a_la_date
    jour = db.func.coalesce(SessionActivite.date_session, SessionActivite.rdv_date)

    requete = (
        db.session.query(
            PresenceActivite.participant_id,
            SessionActivite.secteur,
            db.func.count(PresenceActivite.id),
        )
        .join(SessionActivite, SessionActivite.id == PresenceActivite.session_id)
        .filter(SessionActivite.is_deleted.is_(False))
        # Une séance annulée n'a accueilli personne.
        .filter(db.func.lower(db.func.coalesce(SessionActivite.statut, "")) != "annulee")
        .filter(jour.isnot(None))
        .filter(jour >= debut, jour <= fin)
        # Seules les venues réelles : une absence excusée atteste que la
        # personne n'est pas venue.
        .filter(PresenceActivite.presence_type.in_(VENUES_REELLES))
        .group_by(PresenceActivite.participant_id, SessionActivite.secteur)
    )
    if participant_ids is not None:
        ids = [int(i) for i in participant_ids if i]
        if not ids:
            return {}
        requete = requete.filter(PresenceActivite.participant_id.in_(ids))

    resultat: dict[int, dict[str, int]] = {}
    for pid, secteur, nb in requete.all():
        if not pid:
            continue
        nom = (secteur or "").strip()
        if not nom:
            continue
        compte = resultat.setdefault(int(pid), {})
        compte[nom] = compte.get(nom, 0) + int(nb or 0)
    return resultat


# ---------------------------------------------------------------------------
# « Numerique » et « Numérique » sont le même secteur
# ---------------------------------------------------------------------------

def table_de_canonisation() -> dict[str, str]:
    """Clé sans accents ni casse -> libellé officiel du secteur.

    Les secteurs sont des chaînes libres dans ``SessionActivite.secteur`` et
    ``Participant.created_secteur``. Une majuscule ou un accent de travers
    créerait un septième secteur fantôme dans le tableau, avec sa part
    d'argent dedans — exactement le doublon qu'on a déjà combattu sur les
    villes et les quartiers. On ramène donc chaque écriture à son libellé
    officiel.
    """
    from app.secteurs import get_secteur_labels
    from app.services.recherche_texte import sans_accent

    table: dict[str, str] = {}
    try:
        libelles = get_secteur_labels(active_only=False)
    except Exception:  # noqa: BLE001 — un référentiel indisponible ne casse pas un calcul
        libelles = []
    for libelle in libelles:
        cle = (sans_accent(libelle) or "").strip()
        if cle:
            table.setdefault(cle, libelle)
    return table


def canoniser(secteur: str | None, table: dict[str, str] | None = None) -> str:
    """Le libellé officiel, ou l'écriture d'origine si le secteur est inconnu.

    On ne jette JAMAIS un secteur qu'on ne reconnaît pas : il porte de
    l'argent. Mieux vaut le voir apparaître avec son orthographe bancale —
    c'est visible, donc corrigeable — que le voir disparaître.
    """
    from app.services.recherche_texte import sans_accent

    brut = (secteur or "").strip()
    if not brut:
        return ""
    if table is None:
        table = table_de_canonisation()
    return table.get((sans_accent(brut) or "").strip(), brut)


# ---------------------------------------------------------------------------
# La répartition
# ---------------------------------------------------------------------------

def _participations_avec_reglement(annee: int, a_la_date: date | None = None):
    """Les participations de l'année, avec ce qui a été encaissé dessus.

    ``a_la_date`` arrête la photo : on ne retient que les participations
    déjà enregistrées à cette date, et que les versements déjà reçus. Un
    arrêté au 31 décembre qui compterait un chèque de mars ne serait pas
    un arrêté.

    Un seul aller-retour, sous-requête de somme comme l'écran des impayés :
    interroger les paiements ligne à ligne ferait une requête par personne.
    """
    versements = db.session.query(
        Paiement.cotisation_id.label("cid"),
        db.func.coalesce(db.func.sum(Paiement.montant), 0.0).label("regle"),
    )
    if a_la_date is not None:
        versements = versements.filter(Paiement.date_paiement <= a_la_date)
    regle_subq = versements.group_by(Paiement.cotisation_id).subquery()

    requete = (
        db.session.query(Cotisation, db.func.coalesce(regle_subq.c.regle, 0.0))
        .outerjoin(regle_subq, regle_subq.c.cid == Cotisation.id)
        .filter(
            Cotisation.annee_scolaire == annee,
            Cotisation.type_cotisation == "participation",
        )
    )
    if a_la_date is not None:
        requete = requete.filter(Cotisation.date_reference <= a_la_date)
    return requete.all()


def repartition(annee: int, a_la_date: date | None = None) -> dict:
    """La répartition complète de l'année : par personne et par secteur.

    Retourne ``{annee_scolaire, a_la_date, personnes, secteurs, totaux}``.

    ``a_la_date`` rejoue l'année telle qu'elle était connue ce jour-là —
    venues et versements compris. Sans elle, c'est l'état du jour, qui
    continuera de bouger jusqu'au 31 août.

    ``personnes`` détaille CHAQUE participation : combien de venues dans
    quel secteur, et quelle part de son dû et de son encaissé y revient.
    C'est ce détail qui permet de répondre à « pourquoi le Numérique a-t-il
    420 € ? » sans ouvrir trente fiches.

    ``secteurs`` en est la somme, et rien d'autre : les totaux ne sont
    jamais calculés séparément du détail, sinon les deux finissent par
    diverger et plus personne ne sait lequel croire.
    """
    from app.services.cotisations import libelle_annee_scolaire

    lignes = _participations_avec_reglement(annee, a_la_date)
    if not lignes:
        return {
            "annee_scolaire": annee,
            "a_la_date": a_la_date,
            "libelle_annee": libelle_annee_scolaire(annee),
            "personnes": [],
            "secteurs": {},
            "totaux": {"du": 0.0, "regle": 0.0, "nb_personnes": 0, "nb_repli": 0, "venues": 0},
        }

    table = table_de_canonisation()

    # Qui porte chaque participation. Elle est individuelle par construction
    # (même sur un bulletin familial, chaque personne paye la sienne), mais
    # une reprise de données peut en avoir rattaché une à un foyer : on
    # mutualise alors les venues des membres plutôt que de perdre la somme.
    ids_directs = {c.participant_id for c, _ in lignes if c.participant_id}
    foyers = {c.foyer_id for c, _ in lignes if c.foyer_id and not c.participant_id}
    membres_par_foyer: dict[int, list[Participant]] = {}
    if foyers:
        for membre in Participant.query.filter(Participant.foyer_id.in_(list(foyers))).all():
            membres_par_foyer.setdefault(membre.foyer_id, []).append(membre)

    ids_a_compter = set(ids_directs)
    for membres in membres_par_foyer.values():
        ids_a_compter.update(m.id for m in membres)

    venues = venues_par_secteur(annee, ids_a_compter, a_la_date)
    fiches = {
        p.id: p for p in Participant.query.filter(Participant.id.in_(list(ids_a_compter))).all()
    } if ids_a_compter else {}

    personnes: list[dict] = []
    secteurs: dict[str, dict] = {}
    total_du = total_regle = 0.0
    nb_repli = 0

    for cotisation, regle in lignes:
        if cotisation.participant_id:
            titulaires = [fiches.get(cotisation.participant_id)]
        else:
            titulaires = membres_par_foyer.get(cotisation.foyer_id, [])
        titulaires = [t for t in titulaires if t is not None]
        if not titulaires:
            continue
        principal = titulaires[0]

        # Les venues de la personne (ou, pour un foyer, celles de tous ses
        # membres additionnées), ramenées aux libellés officiels.
        compte: dict[str, int] = {}
        for titulaire in titulaires:
            for secteur_brut, nb in venues.get(titulaire.id, {}).items():
                nom = canoniser(secteur_brut, table)
                if nom:
                    compte[nom] = compte.get(nom, 0) + nb

        repli = not compte
        if repli:
            # Aucune venue cette année : tout revient au secteur qui a fait
            # venir la personne — l'ancienne règle, conservée pour ce cas.
            orienteur = ""
            for titulaire in titulaires:
                orienteur = canoniser(titulaire.created_secteur, table)
                if orienteur:
                    break
            compte = {orienteur or SANS_SECTEUR: 1}
            nb_repli += 1

        du = round(float(cotisation.montant_du or 0), 2)
        encaisse = round(float(regle or 0), 2)
        parts_du = decouper(du, compte)
        parts_regle = decouper(encaisse, compte)

        detail = {}
        for nom, nb in sorted(compte.items(), key=lambda kv: (-kv[1], kv[0])):
            detail[nom] = {
                "venues": 0 if repli else nb,
                "du": parts_du.get(nom, 0.0),
                "regle": parts_regle.get(nom, 0.0),
            }
            case = secteurs.setdefault(
                nom, {"du": 0.0, "regle": 0.0, "venues": 0, "nb_personnes": 0}
            )
            case["du"] = round(case["du"] + detail[nom]["du"], 2)
            case["regle"] = round(case["regle"] + detail[nom]["regle"], 2)
            case["venues"] += detail[nom]["venues"]
            case["nb_personnes"] += 1

        total_du = round(total_du + du, 2)
        total_regle = round(total_regle + encaisse, 2)
        personnes.append({
            "participant": principal,
            "titulaires": titulaires,
            "cotisation": cotisation,
            "du": du,
            "regle": encaisse,
            "total_venues": sum(compte.values()) if not repli else 0,
            "repli": repli,
            "secteur_orienteur": canoniser(principal.created_secteur, table),
            "parts": detail,
        })

    personnes.sort(key=lambda p: (
        (p["participant"].nom or "").lower(), (p["participant"].prenom or "").lower()
    ))

    return {
        "annee_scolaire": annee,
        "a_la_date": a_la_date,
        "libelle_annee": libelle_annee_scolaire(annee),
        "personnes": personnes,
        "secteurs": dict(sorted(secteurs.items(), key=lambda kv: (-kv[1]["regle"], kv[0]))),
        "totaux": {
            "du": total_du,
            "regle": total_regle,
            "nb_personnes": len(personnes),
            "nb_repli": nb_repli,
            "venues": sum(case["venues"] for case in secteurs.values()),
        },
    }


# ---------------------------------------------------------------------------
# Figer : l'arrêté
# ---------------------------------------------------------------------------

def arreter(annee: int, date_arrete: date, *, libelle: str | None = None,
            note: str | None = None, user_id: int | None = None):
    """Photographie la répartition à une date, et l'écrit. Elle ne bougera plus.

    Retourne ``(arrete, message)``. L'arrêté vaut ``None`` si le geste a
    été refusé, et le message dit pourquoi — il est destiné à l'écran.

    Trois refus, tous pour la même raison de fond : un arrêté est une pièce
    qu'on ressortira en réunion, il ne doit pas pouvoir mentir.

    - **une date antérieure à la rentrée** : rien ne s'est encore passé,
      la photo serait vide et son titre laisserait croire le contraire ;
    - **une date future** : on photographierait un état qui n'existe pas
      encore. Un « arrêté au 31 août » signé en mars annonce cinq mois
      qui n'ont pas eu lieu ;
    - **un arrêté déjà pris à cette date** : deux pièces différentes
      portant la même date et le même intitulé, c'est la garantie qu'un
      jour quelqu'un cite la mauvaise.
    """
    from app.models import RepartitionArretee, RepartitionArreteeLigne

    debut, fin = bornes_annee_scolaire(annee)
    if date_arrete < debut:
        return None, (
            f"Le {date_arrete:%d/%m/%Y} précède la rentrée du "
            f"{debut:%d/%m/%Y} : il n'y a rien à arrêter."
        )
    if date_arrete > date.today():
        return None, (
            "On ne peut pas arrêter une répartition à une date future : "
            "l'année n'est pas encore allée jusque-là."
        )
    if date_arrete > fin:
        date_arrete = fin

    if RepartitionArretee.query.filter_by(
            annee_scolaire=annee, date_arrete=date_arrete).first():
        return None, (
            f"Un arrêté existe déjà au {date_arrete:%d/%m/%Y} pour "
            f"{libelle_annee(annee)}. Supprimez-le d'abord si vous voulez le refaire."
        )

    vue = repartition(annee, a_la_date=date_arrete)

    arrete = RepartitionArretee(
        annee_scolaire=annee,
        date_arrete=date_arrete,
        libelle=(libelle or "").strip() or None,
        note=(note or "").strip() or None,
        total_du=vue["totaux"]["du"],
        total_regle=vue["totaux"]["regle"],
        cree_par_user_id=user_id,
    )
    db.session.add(arrete)
    db.session.flush()

    for personne in vue["personnes"]:
        fiche = personne["participant"]
        nom = f"{fiche.nom or ''} {fiche.prenom or ''}".strip()
        for secteur, part in personne["parts"].items():
            db.session.add(RepartitionArreteeLigne(
                arrete_id=arrete.id,
                secteur=secteur,
                participant_id=fiche.id,
                participant_nom=nom[:240] or None,
                venues=part["venues"],
                montant_du=part["du"],
                montant_regle=part["regle"],
                repli=personne["repli"],
            ))

    db.session.commit()
    return arrete, f"Répartition arrêtée au {date_arrete:%d/%m/%Y}."


def libelle_annee(annee: int) -> str:
    from app.services.cotisations import libelle_annee_scolaire

    return libelle_annee_scolaire(annee)


def arretes(annee: int | None = None) -> list:
    """Les arrêtés existants, du plus récent au plus ancien."""
    from app.models import RepartitionArretee

    requete = RepartitionArretee.query
    if annee is not None:
        requete = requete.filter_by(annee_scolaire=annee)
    return requete.order_by(
        RepartitionArretee.annee_scolaire.desc(),
        RepartitionArretee.date_arrete.desc(),
    ).all()


def dernier_arrete(annee: int):
    """Le dernier arrêté de l'année, s'il y en a un."""
    liste = arretes(annee)
    return liste[0] if liste else None
