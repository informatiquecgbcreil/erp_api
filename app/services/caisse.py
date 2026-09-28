"""Caisse : état théorique, comptage, dépôts.

Vocabulaire (repris dans le glossaire) :
- fond de caisse : la somme permanente laissée dans la boîte pour rendre
  la monnaie — elle ne se dépose jamais à la banque ;
- théorique : ce qu'il DEVRAIT y avoir dans la boîte d'après les saisies
  (fond + encaissements − dépôts ± ajustements) ;
- arrêté de caisse (comptage) : on compte physiquement et on compare au
  théorique ; l'écart éventuel est tracé et le théorique réaligné ;
- dépôt : la recette part à la banque, le fond reste.

Les encaissements sont LUS depuis les écritures d'encaissement (espèces /
chèque, y compris les sommes reçues sur un bulletin avant la fiche et les
règlements de location) et les dons en numéraire non annulés : la caisse
n'exige aucune re-saisie. Un montant non fini (NaN, infini) hérité d'une
ancienne version est exclu du calcul et signalé (Contrôle -> Anomalies de
montants) au lieu de rendre toute la caisse incalculable.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import CaisseMouvement, Don, Encaissement


# Clé du verrou transactionnel PostgreSQL qui sérialise les écritures de caisse.
VERROU_CAISSE = 724001


def verrouiller_caisse() -> None:
    """Sérialise fond, comptage et dépôt jusqu'à la fin de la transaction.

    Sans lui, deux dépôts envoyés en même temps (double clic) lisaient le même
    théorique et passaient tous les deux : caisse négative. À appeler AVANT de
    lire l'état de la caisse ; le verrou tombe au commit ou au rollback.
    SQLite (tests, poste isolé) sérialise déjà les écritures.
    """
    if db.session.get_bind().dialect.name == "postgresql":
        db.session.execute(db.text("SELECT pg_advisory_xact_lock(:cle)"), {"cle": VERROU_CAISSE})


def _somme(query) -> float:
    return round(float(query.scalar() or 0.0), 2)


BORNE = 1_000_000


def _fini(colonne):
    """Exclut NaN et l'infini : NaN est « plus grand que tout » pour
    PostgreSQL, et stocké NULL par SQLite (déjà ignoré par SUM)."""
    return colonne.between(-BORNE, BORNE)


def encaissements(canal: str) -> float:
    """Total des encaissements du canal (especes|cheque) depuis l'origine."""
    regl = _somme(
        db.session.query(db.func.sum(Encaissement.montant)).filter(
            Encaissement.mode == canal,
            Encaissement.hors_caisse.is_(False),
            _fini(Encaissement.montant),
        )
    )
    dons = _somme(
        db.session.query(db.func.sum(Don.montant)).filter(
            Don.mode_versement == canal,
            Don.forme_don == "numeraire",
            # Un don annulé AVEC correction de caisse reste compté : la
            # correction datée du jour l'équilibre, l'historique ne bouge pas.
            # (Les annulations antérieures, sans correction, restent exclues.)
            db.or_(Don.est_annule.is_(False), Don.annulation_mouvement_id.isnot(None)),
            _fini(Don.montant),
        )
    )
    return round(regl + dons, 2)


def fond_de_caisse() -> float:
    """Le dernier fond de caisse défini (0 si jamais défini)."""
    dernier = (
        CaisseMouvement.query
        .filter(CaisseMouvement.type_mouvement == "fond", _fini(CaisseMouvement.montant))
        .order_by(CaisseMouvement.date_mouvement.desc(), CaisseMouvement.id.desc())
        .first()
    )
    return round(float(dernier.montant), 2) if dernier else 0.0


def _mouvements_somme(type_mouvement: str, canal: str) -> float:
    return _somme(
        db.session.query(db.func.sum(CaisseMouvement.montant)).filter(
            CaisseMouvement.type_mouvement == type_mouvement,
            CaisseMouvement.canal == canal,
            _fini(CaisseMouvement.montant),
        )
    )


def etat_caisse() -> dict:
    """L'état complet de la caisse pour l'écran."""
    enc_especes = encaissements("especes")
    enc_cheques = encaissements("cheque")
    depots_especes = _mouvements_somme("depot", "especes")
    depots_cheques = _mouvements_somme("depot", "cheque")
    ajust_especes = _mouvements_somme("ajustement", "especes")
    fond = fond_de_caisse()

    theorique_especes = round(fond + enc_especes - depots_especes + ajust_especes, 2)
    ajust_cheques = _mouvements_somme("ajustement", "cheque")
    cheques_en_attente = round(enc_cheques - depots_cheques + ajust_cheques, 2)

    dernier_comptage = (
        CaisseMouvement.query
        .filter(CaisseMouvement.type_mouvement == "comptage")
        .order_by(CaisseMouvement.date_mouvement.desc(), CaisseMouvement.id.desc())
        .first()
    )

    return {
        "fond": fond,
        "encaissements_especes": enc_especes,
        "encaissements_cheques": enc_cheques,
        "depots_especes": depots_especes,
        "depots_cheques": depots_cheques,
        "ajustements_especes": ajust_especes,
        "theorique_especes": theorique_especes,
        "recette_deposable": round(max(0.0, theorique_especes - fond), 2),
        "cheques_en_attente": cheques_en_attente,
        "dernier_comptage": dernier_comptage,
        # Sommes anciennes dont le mode ou la prise en compte reste à trancher.
        # (Une somme annulée par contre-passation n'est plus à trancher.)
        "a_qualifier": _somme(db.session.query(db.func.sum(Encaissement.montant)).filter(
            Encaissement.a_qualifier.is_(True), Encaissement.origine_id.is_(None),
            ~Encaissement.contre_passations.any(), _fini(Encaissement.montant))),
        "nb_a_qualifier": Encaissement.query.filter(
            Encaissement.a_qualifier.is_(True), Encaissement.origine_id.is_(None),
            ~Encaissement.contre_passations.any()).count(),
        "nb_a_rapprocher": _nb_a_rapprocher(),
        "nb_anomalies": nombre_anomalies(),
    }


def _nb_a_rapprocher() -> int:
    from app.models import RapprochementBulletin
    try:
        return RapprochementBulletin.query.filter(db.or_(
            db.and_(RapprochementBulletin.classement == "a_rapprocher", RapprochementBulletin.decision.is_(None)),
            RapprochementBulletin.controle.in_(("doublon_a_annuler", "a_verifier")))).count()
    except Exception:  # noqa: BLE001 — table pas encore migrée
        db.session.rollback()
        return 0


def enregistrer_comptage(montant_constate: float, *, commentaire: str | None = None,
                         user_id: int | None = None, jour: date | None = None,
                         jeton: str | None = None) -> CaisseMouvement:
    """Arrêté de caisse : compare le constaté au théorique, trace l'écart,
    et réaligne le théorique via un ajustement automatique si besoin."""
    jour = jour or date.today()
    verrouiller_caisse()
    theorique = etat_caisse()["theorique_especes"]
    ecart = round(montant_constate - theorique, 2)

    comptage = CaisseMouvement(
        type_mouvement="comptage", canal="especes",
        montant=montant_constate, ecart=ecart,
        date_mouvement=jour, commentaire=commentaire,
        created_by_user_id=user_id, jeton=jeton,
    )
    db.session.add(comptage)
    if abs(ecart) >= 0.01:
        db.session.add(CaisseMouvement(
            type_mouvement="ajustement", canal="especes",
            montant=ecart, date_mouvement=jour,
            commentaire=f"Écart constaté à l'arrêté de caisse du {jour.strftime('%d/%m/%Y')}",
            created_by_user_id=user_id,
        ))
    db.session.commit()
    return comptage


def journal(limite: int = 100) -> list[CaisseMouvement]:
    return (
        CaisseMouvement.query
        .order_by(CaisseMouvement.date_mouvement.desc(), CaisseMouvement.id.desc())
        .limit(limite)
        .all()
    )


def nombre_anomalies() -> int:
    """Montants non finis restant dans les tables d'argent (voir anomalies)."""
    from app.services.anomalies_montants import compter
    try:
        return compter()
    except Exception:
        return 0


def ajuster(montant: float, motif: str, *, user_id: int | None = None, jour: date | None = None) -> CaisseMouvement:
    """Ajustement manuel du théorique d'espèces, motivé (audit 2.2).

    Pour ce que le comptage ne couvre pas : erreur de rendu de monnaie
    constatée, achat payé en espèces sur la caisse. Jamais pour corriger un
    règlement : celui-ci se contre-passe là où il a été saisi.
    """
    motif = (motif or "").strip()
    if len(motif) < 3:
        raise ValueError("Le motif de l'ajustement est obligatoire.")
    if abs(montant) < 0.01:
        raise ValueError("Un ajustement de 0 € n'a pas de sens.")
    verrouiller_caisse()
    mouvement = CaisseMouvement(
        type_mouvement="ajustement", canal="especes", montant=round(montant, 2),
        date_mouvement=jour or date.today(), commentaire=f"Ajustement manuel : {motif}"[:255],
        created_by_user_id=user_id,
    )
    db.session.add(mouvement)
    db.session.commit()
    return mouvement
