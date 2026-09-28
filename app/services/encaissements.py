"""Encaissements : chaque somme reçue est écrite au moment où elle l'est.

Règles (audit C2, 2.2, 2.4) :

- **Écriture immédiate.** Un encaissement existe dès que l'argent est reçu,
  même sans fiche ni cotisation (bulletin d'inscription saisi à l'accueil,
  location de salle). La caisse le lit directement : il n'y a plus d'argent
  « posé sur un bulletin » que la caisse ignore.
- **Un encaissement = un mode, une date, un montant.** 20 € en espèces et
  50 € en chèque sont deux encaissements : le bordereau reste juste.
- **Ventilation séparée.** La part affectée aux cotisations (``Paiement``)
  peut venir plus tard, à la création de la fiche. Elle recopie le mode et
  la date de l'encaissement. Ce qui dépasse le dû reste un trop-perçu
  visible, jamais perdu. Rejouer la ventilation ne double rien : elle ne
  porte que sur le reste non ventilé.
- **Correction par contre-passation**, jamais par modification ni
  suppression : un encaissement négatif lié à l'original, motif et auteur
  obligatoires, ventilations inversées.
- **Données anciennes** : un montant ou un mode que l'application ne peut
  pas reconstituer est marqué « à qualifier » et reste hors du théorique de
  caisse jusqu'à ce qu'une personne habilitée tranche.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import MODES_PAIEMENT, Cotisation, Encaissement, Paiement
from app.utils.montants import parse_montant


class EncaissementErreur(ValueError):
    """Opération refusée, avec un message destiné à l'accueil."""


class DejaEnregistre(EncaissementErreur):
    """Le même formulaire a déjà été enregistré (double clic, renvoi)."""


# ---------------------------------------------------------------------------
# Verrous
# ---------------------------------------------------------------------------

def verrouiller(objet: str, identifiant: int) -> None:
    """Sérialise les écritures d'argent sur un même objet jusqu'au commit.

    Deux encaissements simultanés sur un même bulletin sont tous les deux
    conservés (ce sont deux sommes), mais leur ventilation ne doit pas lire
    le même « reste dû » : sans verrou, la même ligne était soldée deux fois.
    """
    if db.session.get_bind().dialect.name != "postgresql":
        return  # SQLite sérialise déjà les écritures.
    import zlib
    cle = zlib.crc32(objet.encode()) & 0x7FFFFFFF
    db.session.execute(db.text("SELECT pg_advisory_xact_lock(:a, :b)"), {"a": cle, "b": int(identifiant)})


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------

def _jeton_propre(jeton: str | None) -> str | None:
    jeton = (jeton or "").strip()
    return jeton[:64] if len(jeton) >= 16 else None


def deja_enregistre(jeton: str | None) -> Encaissement | None:
    jeton = _jeton_propre(jeton)
    return Encaissement.query.filter_by(jeton=jeton).first() if jeton else None


def enregistrer(
    montant,
    mode: str,
    *,
    date_encaissement: date | None = None,
    inscription=None,
    participant_id: int | None = None,
    foyer_id: int | None = None,
    reservation=None,
    libelle: str | None = None,
    commentaire: str | None = None,
    user_id: int | None = None,
    jeton: str | None = None,
) -> Encaissement:
    """Écrit une somme reçue (non commité : l'appelant tient la transaction)."""
    valeur = parse_montant(montant)
    if valeur is None or valeur <= 0:
        raise EncaissementErreur("Le montant encaissé doit être un nombre supérieur à 0 €.")
    mode = (mode or "").strip()
    if mode not in MODES_PAIEMENT:
        raise EncaissementErreur("Mode de règlement inconnu.")
    jeton = _jeton_propre(jeton)
    existant = deja_enregistre(jeton)
    if existant is not None:
        raise DejaEnregistre("Ce règlement a déjà été enregistré (double envoi du formulaire).")
    encaissement = Encaissement(
        montant=valeur,
        mode=mode,
        date_encaissement=date_encaissement or date.today(),
        inscription_annuelle_id=getattr(inscription, "id", None),
        participant_id=participant_id or getattr(inscription, "participant_id", None),
        foyer_id=foyer_id,
        reservation_id=getattr(reservation, "id", None),
        libelle=(libelle or "")[:160] or None,
        commentaire=(commentaire or "")[:255] or None,
        created_by_user_id=user_id,
        jeton=jeton,
    )
    db.session.add(encaissement)
    try:
        with db.session.begin_nested():
            db.session.flush()
    except IntegrityError:
        # Deux envois concurrents du même formulaire : le second perd.
        raise DejaEnregistre("Ce règlement a déjà été enregistré (double envoi du formulaire).") from None
    return encaissement


# ---------------------------------------------------------------------------
# Ventilation sur les cotisations
# ---------------------------------------------------------------------------

def ventiler(encaissement: Encaissement, cotisations: list[Cotisation], *, user_id: int | None = None,
             plafonner: bool = True) -> list[Paiement]:
    """Affecte le reste non ventilé de l'encaissement aux cotisations, dans
    l'ordre donné (l'adhésion d'abord). ``plafonner`` : jamais au-delà du reste
    dû d'une ligne ; le surplus reste un trop-perçu visible."""
    restant = encaissement.reste_a_ventiler
    crees: list[Paiement] = []
    for cotisation in cotisations:
        if restant <= 0.009:
            break
        part = min(restant, cotisation.reste_du) if plafonner else restant
        part = round(part, 2)
        if part <= 0.009:
            continue
        # Relié à son encaissement DÈS la construction : une lecture automatique
        # de la base (autoflush) ne doit jamais voir un versement orphelin.
        versement = Paiement(
            montant=part,
            date_paiement=encaissement.date_encaissement,
            mode=encaissement.mode,
            commentaire=encaissement.commentaire,
            created_by_user_id=user_id,
            encaissement=encaissement,
        )
        # Par la relation : la collection déjà chargée voit le versement (sinon
        # ``reste_du`` répondrait l'ancien total et on ventilerait deux fois).
        cotisation.paiements.append(versement)
        crees.append(versement)
        restant = round(restant - part, 2)
    db.session.flush()
    return crees


def encaissements_du_bulletin(inscription) -> list[Encaissement]:
    return (Encaissement.query
            .filter(Encaissement.inscription_annuelle_id == inscription.id)
            .order_by(Encaissement.date_encaissement.asc(), Encaissement.id.asc())
            .all())


def ventiler_bulletin(inscription, *, user_id: int | None = None) -> list[Paiement]:
    """Rattache aux cotisations l'argent reçu sur le bulletin et pas encore
    ventilé, encaissement par encaissement (mode et date conservés).
    Idempotent : ne porte que sur les restes."""
    from app.services.inscriptions_annuelles import cotisations_du_bulletin
    cotisations = cotisations_du_bulletin(inscription)
    if not cotisations:
        return []
    verrouiller("bulletin", inscription.id)
    crees: list[Paiement] = []
    for encaissement in encaissements_du_bulletin(inscription):
        if encaissement.reste_a_ventiler > 0.009:
            crees += ventiler(encaissement, cotisations, user_id=user_id)
    return crees


def trop_percu(encaissements) -> float:
    return round(sum(e.reste_a_ventiler for e in encaissements), 2)


# ---------------------------------------------------------------------------
# Contre-passation
# ---------------------------------------------------------------------------

def contre_passer(encaissement: Encaissement, motif: str, *, user_id: int | None = None,
                  jeton: str | None = None, date_correction: date | None = None) -> Encaissement:
    """Annule un encaissement par une écriture inverse (non commité).

    Le montant, le mode et les rattachements de l'original sont repris en
    négatif, ses ventilations inversées cotisation par cotisation. L'original
    reste tel quel dans le livre de caisse.
    """
    motif = (motif or "").strip()
    if len(motif) < 3:
        raise EncaissementErreur("Indiquez le motif de la correction (il sera conservé au journal).")
    if encaissement.origine_id is not None or encaissement.montant <= 0:
        raise EncaissementErreur("Une contre-passation ne se contre-passe pas : enregistrez un nouvel encaissement.")
    verrouiller("encaissement", encaissement.id)
    db.session.refresh(encaissement)
    if encaissement.est_contre_passe:
        raise EncaissementErreur("Cet encaissement a déjà été contre-passé.")
    jeton = _jeton_propre(jeton)
    if deja_enregistre(jeton) is not None:
        raise DejaEnregistre("Cette correction a déjà été enregistrée (double envoi du formulaire).")

    inverse = Encaissement(
        montant=-round(float(encaissement.montant), 2),
        mode=encaissement.mode,
        date_encaissement=date_correction or date.today(),
        inscription_annuelle_id=encaissement.inscription_annuelle_id,
        participant_id=encaissement.participant_id,
        foyer_id=encaissement.foyer_id,
        reservation_id=encaissement.reservation_id,
        libelle=encaissement.libelle,
        origine_id=encaissement.id,
        motif=motif[:255],
        a_qualifier=encaissement.a_qualifier,
        hors_caisse=encaissement.hors_caisse,
        created_by_user_id=user_id,
        jeton=jeton,
    )
    db.session.add(inverse)
    par_cotisation: dict[int, float] = {}
    for v in encaissement.ventilations:
        par_cotisation[v.cotisation_id] = round(par_cotisation.get(v.cotisation_id, 0.0) + float(v.montant), 2)
    for cotisation_id, montant in par_cotisation.items():
        if abs(montant) <= 0.009:
            continue
        cotisation = db.session.get(Cotisation, cotisation_id)
        versement = Paiement(montant=-montant, date_paiement=inverse.date_encaissement,
                             mode=encaissement.mode, commentaire=f"Contre-passation : {motif}"[:255],
                             created_by_user_id=user_id, encaissement=inverse)
        cotisation.paiements.append(versement)
    try:
        with db.session.begin_nested():
            db.session.flush()
    except IntegrityError:
        raise DejaEnregistre("Cette correction a déjà été enregistrée (double envoi du formulaire).") from None
    return inverse


# ---------------------------------------------------------------------------
# Qualification des sommes anciennes
# ---------------------------------------------------------------------------

def a_qualifier() -> list[Encaissement]:
    return (Encaissement.query.filter(Encaissement.a_qualifier.is_(True), Encaissement.origine_id.is_(None),
                                      ~Encaissement.contre_passations.any())
            .order_by(Encaissement.date_encaissement.asc(), Encaissement.id.asc()).all())


def qualifier(encaissement: Encaissement, parts: list[tuple[str, float]], *, dans_caisse: bool,
              user_id: int | None = None) -> list[Encaissement]:
    """Tranche une somme ancienne : mode(s) réel(s) et prise en compte en caisse.

    ``parts`` : [(mode, montant)], dont la somme doit égaler le montant. Une
    somme mixte (espèces + chèque) est découpée en autant d'encaissements, la
    ventilation sur les cotisations étant refaite à l'identique du total.
    ``dans_caisse`` : faux si l'argent a déjà été absorbé par un comptage de
    caisse (écart alors constaté), vrai s'il doit entrer dans le théorique.
    """
    if not encaissement.a_qualifier or encaissement.origine_id is not None:
        raise EncaissementErreur("Cet encaissement n'est pas à qualifier.")
    if encaissement.est_contre_passe:
        raise EncaissementErreur("Cet encaissement a été contre-passé : rien à qualifier.")
    propres = []
    for mode, montant in parts:
        valeur = parse_montant(montant)
        if mode not in MODES_PAIEMENT or valeur is None or valeur <= 0:
            raise EncaissementErreur("Chaque part doit avoir un mode connu et un montant positif.")
        propres.append((mode, valeur))
    if not propres or abs(sum(v for _, v in propres) - float(encaissement.montant)) > 0.009:
        raise EncaissementErreur(f"La somme des parts doit être exactement {encaissement.montant:.2f} €.")
    verrouiller("encaissement", encaissement.id)
    # Double clic ou deux personnes en même temps : seule la première
    # qualification passe. Mise à jour conditionnelle (valable sur SQLite
    # comme sur PostgreSQL) : l'autre voit la somme déjà qualifiée.
    table = Encaissement.__table__
    reclame = db.session.execute(table.update().where(table.c.id == encaissement.id,
                                                      table.c.a_qualifier.is_(True)).values(a_qualifier=False))
    if reclame.rowcount != 1:
        raise DejaEnregistre("Cette somme a déjà été qualifiée.")
    db.session.refresh(encaissement)

    # Les cotisations touchées par l'ancienne ventilation, dans leur ordre.
    cotisations: list[Cotisation] = []
    for v in encaissement.ventilations:
        if v.cotisation not in cotisations:
            cotisations.append(v.cotisation)
    for v in list(encaissement.ventilations):
        # Une ventilation n'est qu'une affectation dérivée de l'encaissement
        # (pas une pièce de caisse) : on la refait à l'identique du total.
        v.cotisation.paiements.remove(v)
        encaissement.ventilations.remove(v)
        db.session.delete(v)
    db.session.flush()

    note = (encaissement.note_qualification or "")
    resultat = []
    premier_mode, premier_montant = propres[0]
    encaissement.mode = premier_mode
    encaissement.montant = premier_montant
    encaissement.a_qualifier = False
    encaissement.hors_caisse = not dans_caisse
    encaissement.note_qualification = (note + " · qualifié" + ("" if dans_caisse else " (hors caisse)"))[:255]
    resultat.append(encaissement)
    for mode, montant in propres[1:]:
        part = Encaissement(
            montant=montant, mode=mode, date_encaissement=encaissement.date_encaissement,
            inscription_annuelle_id=encaissement.inscription_annuelle_id,
            participant_id=encaissement.participant_id, foyer_id=encaissement.foyer_id,
            reservation_id=encaissement.reservation_id, libelle=encaissement.libelle,
            commentaire=encaissement.commentaire, hors_caisse=not dans_caisse,
            note_qualification=f"Part de l'encaissement ancien n° {encaissement.id}",
            created_by_user_id=user_id,
        )
        db.session.add(part)
        resultat.append(part)
    db.session.flush()
    for part in resultat:
        if cotisations:
            ventiler(part, cotisations, user_id=user_id)
    return resultat


# ---------------------------------------------------------------------------
# Invariant : pas de versement sans encaissement
# ---------------------------------------------------------------------------

def _versements_sans_encaissement(session, _flush_context, _instances):
    """Tout versement enregistré sans encaissement en reçoit un, identique.

    Le circuit normal passe par ``enregistrer`` puis ``ventiler`` ; ce filet
    garantit que même un chemin de code qui créerait un ``Paiement`` à la
    main (ancienne fonction, import, script) ne fait jamais entrer d'argent
    que la caisse ignorerait.
    """
    for obj in list(session.new):
        if not isinstance(obj, Paiement) or obj.encaissement is not None or obj.encaissement_id:
            continue
        cotisation = obj.cotisation or (session.get(Cotisation, obj.cotisation_id) if obj.cotisation_id else None)
        encaissement = Encaissement(
            montant=obj.montant, mode=obj.mode or "especes",
            date_encaissement=obj.date_paiement or date.today(),
            participant_id=getattr(cotisation, "participant_id", None),
            foyer_id=getattr(cotisation, "foyer_id", None),
            commentaire=obj.commentaire, created_by_user_id=obj.created_by_user_id,
        )
        session.add(encaissement)
        obj.encaissement = encaissement
        import logging
        logging.getLogger(__name__).warning(
            "Versement créé hors du circuit d'encaissement : écriture de caisse ajoutée automatiquement.")


def installer_invariant_versements() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    if not event.contains(Session, "before_flush", _versements_sans_encaissement):
        event.listen(Session, "before_flush", _versements_sans_encaissement)
