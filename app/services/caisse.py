"""Caisse : état théorique, comptage, dépôts.

Vocabulaire (repris dans le glossaire) :
- fond de caisse : la somme permanente laissée dans la boîte pour rendre
  la monnaie — elle ne se dépose jamais à la banque ;
- théorique : ce qu'il DEVRAIT y avoir dans la boîte d'après les saisies
  (fond + encaissements − dépôts ± ajustements) ;
- arrêté de caisse (comptage) : on compte physiquement et on compare au
  théorique ; l'écart éventuel est tracé et le théorique réaligné ;
- dépôt : la recette part à la banque, le fond reste.

Les encaissements sont LUS depuis les règlements (Paiement espèces/chèque)
et les dons en numéraire non annulés : la caisse n'exige aucune re-saisie.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import CaisseMouvement, Don, Paiement


def _somme(query) -> float:
    return round(float(query.scalar() or 0.0), 2)


def encaissements(canal: str) -> float:
    """Total des encaissements du canal (especes|cheque) depuis l'origine."""
    regl = _somme(
        db.session.query(db.func.sum(Paiement.montant)).filter(Paiement.mode == canal)
    )
    dons = _somme(
        db.session.query(db.func.sum(Don.montant)).filter(
            Don.mode_versement == canal,
            Don.forme_don == "numeraire",
            Don.est_annule.is_(False),
        )
    )
    return round(regl + dons, 2)


def fond_de_caisse() -> float:
    """Le dernier fond de caisse défini (0 si jamais défini)."""
    dernier = (
        CaisseMouvement.query
        .filter(CaisseMouvement.type_mouvement == "fond")
        .order_by(CaisseMouvement.date_mouvement.desc(), CaisseMouvement.id.desc())
        .first()
    )
    return round(float(dernier.montant), 2) if dernier else 0.0


def _mouvements_somme(type_mouvement: str, canal: str) -> float:
    return _somme(
        db.session.query(db.func.sum(CaisseMouvement.montant)).filter(
            CaisseMouvement.type_mouvement == type_mouvement,
            CaisseMouvement.canal == canal,
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
    cheques_en_attente = round(enc_cheques - depots_cheques, 2)

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
    }


def enregistrer_comptage(montant_constate: float, *, commentaire: str | None = None,
                         user_id: int | None = None, jour: date | None = None) -> CaisseMouvement:
    """Arrêté de caisse : compare le constaté au théorique, trace l'écart,
    et réaligne le théorique via un ajustement automatique si besoin."""
    jour = jour or date.today()
    theorique = etat_caisse()["theorique_especes"]
    ecart = round(montant_constate - theorique, 2)

    comptage = CaisseMouvement(
        type_mouvement="comptage", canal="especes",
        montant=montant_constate, ecart=ecart,
        date_mouvement=jour, commentaire=commentaire,
        created_by_user_id=user_id,
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
