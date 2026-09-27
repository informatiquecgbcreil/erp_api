"""Encaissements : toute somme reçue devient une écriture (audit C2, 2.2, 2.4).

Reprise des données anciennes, idempotente (clé ``source_ancienne``) :

1. Chaque versement existant (``paiement``) reçoit un encaissement identique
   (montant, mode, date, auteur) : le théorique de caisse ne bouge pas d'un
   centime. Un versement au montant non fini (NaN, infini) n'en reçoit pas :
   il est listé dans Contrôle -> Anomalies de montants pour être traité.
2. L'argent noté sur un bulletin sans aucun versement enregistré en face
   (fiche jamais créée, ou cotisations jamais générées) devient un
   encaissement « à qualifier » : son mode réel n'est pas connu (le bulletin
   ne gardait que le dernier mode saisi) et il reste HORS du théorique de
   caisse jusqu'à décision, car un comptage a pu l'absorber déjà.
3. Les règlements de location notés par date d'acompte / date de solde
   deviennent des encaissements « à qualifier » (mode inconnu, montant du
   solde reconstitué d'après le prix actuel), hors caisse.

Rien n'est inventé : les cas non reconstituables sont signalés, pas
régularisés. Rejouer la migration ne crée aucun doublon.
"""
import math
from datetime import date, datetime

from alembic import op
import sqlalchemy as sa

revision = "b5d8e3a1f264"
down_revision = "a4c7e2f9d153"
branch_labels = None
depends_on = None


def _fini(valeur):
    try:
        return valeur is not None and math.isfinite(float(valeur))
    except (TypeError, ValueError):
        return False


def _jour(valeur):
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    if isinstance(valeur, str) and valeur:
        return date.fromisoformat(valeur[:10])
    return None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "encaissement" not in tables:
        op.create_table(
            "encaissement",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("montant", sa.Float(), nullable=False),
            sa.Column("mode", sa.String(20), nullable=False, server_default="especes"),
            sa.Column("date_encaissement", sa.Date(), nullable=False),
            sa.Column("inscription_annuelle_id", sa.Integer(),
                      sa.ForeignKey("inscription_annuelle.id", ondelete="SET NULL"), nullable=True),
            sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="SET NULL"), nullable=True),
            sa.Column("foyer_id", sa.Integer(), sa.ForeignKey("foyer.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservation.id", ondelete="SET NULL"), nullable=True),
            sa.Column("libelle", sa.String(160), nullable=True),
            sa.Column("commentaire", sa.String(255), nullable=True),
            sa.Column("origine_id", sa.Integer(), sa.ForeignKey("encaissement.id"), nullable=True),
            sa.Column("motif", sa.String(255), nullable=True),
            sa.Column("a_qualifier", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("note_qualification", sa.String(255), nullable=True),
            sa.Column("hors_caisse", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_ancienne", sa.String(60), nullable=True, unique=True),
            sa.Column("jeton", sa.String(64), nullable=True, unique=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for colonne in ("date_encaissement", "inscription_annuelle_id", "participant_id", "foyer_id",
                        "reservation_id", "origine_id", "a_qualifier"):
            op.create_index(f"ix_encaissement_{colonne}", "encaissement", [colonne])
    colonnes = {c["name"] for c in sa.inspect(bind).get_columns("paiement")}
    if "encaissement_id" not in colonnes:
        with op.batch_alter_table("paiement") as batch:
            batch.add_column(sa.Column("encaissement_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_paiement_encaissement", "encaissement", ["encaissement_id"], ["id"],
                                     ondelete="SET NULL")
            batch.create_index("ix_paiement_encaissement_id", ["encaissement_id"])
    colonnes = {c["name"] for c in sa.inspect(bind).get_columns("caisse_mouvement")}
    if "jeton" not in colonnes:
        with op.batch_alter_table("caisse_mouvement") as batch:
            batch.add_column(sa.Column("jeton", sa.String(64), nullable=True))
            batch.create_unique_constraint("uq_caisse_mouvement_jeton", ["jeton"])
    reprendre_donnees(bind)
    if bind.dialect.name == "postgresql":
        existe = bind.execute(sa.text("SELECT 1 FROM pg_constraint WHERE conname = 'ck_encaissement_montant_fini'")).scalar()
        if not existe:
            op.create_check_constraint("ck_encaissement_montant_fini", "encaissement",
                                       "montant BETWEEN -1000000 AND 1000000")


def _inserer(bind, **valeurs):
    """Insère un encaissement s'il n'existe pas déjà pour cette source."""
    deja = bind.execute(sa.text("SELECT id FROM encaissement WHERE source_ancienne = :s"),
                        {"s": valeurs["source_ancienne"]}).scalar()
    if deja:
        return deja
    valeurs.setdefault("a_qualifier", False)
    valeurs.setdefault("hors_caisse", False)
    valeurs.setdefault("created_at", datetime.utcnow())
    for cle in ("inscription_annuelle_id", "participant_id", "foyer_id", "reservation_id", "libelle",
                "commentaire", "note_qualification", "created_by_user_id"):
        valeurs.setdefault(cle, None)
    bind.execute(sa.text(
        "INSERT INTO encaissement (montant, mode, date_encaissement, inscription_annuelle_id, participant_id, "
        "foyer_id, reservation_id, libelle, commentaire, a_qualifier, note_qualification, hors_caisse, "
        "source_ancienne, created_by_user_id, created_at) VALUES (:montant, :mode, :date_encaissement, "
        ":inscription_annuelle_id, :participant_id, :foyer_id, :reservation_id, :libelle, :commentaire, "
        ":a_qualifier, :note_qualification, :hors_caisse, :source_ancienne, :created_by_user_id, :created_at)"
    ), valeurs)
    return bind.execute(sa.text("SELECT id FROM encaissement WHERE source_ancienne = :s"),
                        {"s": valeurs["source_ancienne"]}).scalar()


def reprendre_donnees(bind):
    # 1. Versements existants : un encaissement identique chacun.
    lignes = bind.execute(sa.text(
        "SELECT p.id, p.montant, p.mode, p.date_paiement, p.commentaire, p.created_by_user_id, p.created_at, "
        "c.participant_id, c.foyer_id FROM paiement p JOIN cotisation c ON c.id = p.cotisation_id "
        "WHERE p.encaissement_id IS NULL"
    )).fetchall()
    for pid, montant, mode, jour, commentaire, auteur, cree, participant_id, foyer_id in lignes:
        if not _fini(montant) or _jour(jour) is None:
            continue  # Anomalie : traitée depuis Contrôle -> Anomalies de montants.
        eid = _inserer(bind, montant=float(montant), mode=mode or "especes", date_encaissement=_jour(jour),
                       participant_id=participant_id, foyer_id=foyer_id, commentaire=commentaire,
                       created_by_user_id=auteur, created_at=cree or datetime.utcnow(),
                       source_ancienne=f"paiement:{pid}")
        bind.execute(sa.text("UPDATE paiement SET encaissement_id = :e WHERE id = :p"), {"e": eid, "p": pid})

    # 2. Argent noté sur un bulletin sans aucun versement en face.
    bulletins = bind.execute(sa.text(
        "SELECT id, annee_scolaire, participant_id, foyer_id, reglement_montant, reglement_mode, reglement_date, "
        "date_inscription FROM inscription_annuelle WHERE reglement_montant > 0"
    )).fetchall()
    for bid, annee, participant_id, foyer_id, montant, mode, jour, inscrit_le in bulletins:
        if not _fini(montant):
            continue
        if bind.execute(sa.text("SELECT 1 FROM encaissement WHERE inscription_annuelle_id = :b"), {"b": bid}).first():
            continue  # Déjà suivi en encaissements : rien d'ancien à reprendre.
        if participant_id is not None:
            requete = ("SELECT count(*) FROM paiement p JOIN cotisation c ON c.id = p.cotisation_id "
                       "WHERE c.annee_scolaire = :a AND (c.participant_id = :p"
                       + (" OR c.foyer_id = :f)" if foyer_id is not None else ")"))
            verse = bind.execute(sa.text(requete), {"a": annee, "p": participant_id, "f": foyer_id}).scalar()
            if verse:
                continue  # Déjà reporté en versements lors de la création de la fiche.
        libelle_mode = {"especes": "espèces", "cheque": "chèque", "carte": "carte", "virement": "virement"}.get(mode or "", mode or "non noté")
        _inserer(bind, montant=round(float(montant), 2), mode="inconnu",
                 date_encaissement=_jour(jour) or _jour(inscrit_le) or date.today(),
                 inscription_annuelle_id=bid, participant_id=participant_id,
                 libelle=f"Inscription annuelle {annee}-{annee + 1}", a_qualifier=True, hors_caisse=True,
                 note_qualification=(f"Somme notée sur le bulletin avant la fiche ; dernier mode saisi : {libelle_mode}"
                                     " ; le règlement a pu être fait en plusieurs modes.")[:255],
                 source_ancienne=f"bulletin:{bid}")

    # 3. Règlements de location notés par dates.
    if "reservation" in set(sa.inspect(bind).get_table_names()):
        reservations = bind.execute(sa.text(
            "SELECT id, reference, acompte_montant, acompte_regle_le, solde_regle_le, gratuite, montant_manuel, "
            "montant_calcule FROM reservation WHERE acompte_regle_le IS NOT NULL OR solde_regle_le IS NOT NULL"
        )).fetchall()
        for rid, reference, acompte, acompte_le, solde_le, gratuite, manuel, calcule in reservations:
            if bind.execute(sa.text("SELECT 1 FROM encaissement WHERE reservation_id = :r "
                                    "AND (source_ancienne IS NULL OR source_ancienne NOT LIKE 'reservation-%')"),
                            {"r": rid}).first():
                continue  # Règlements déjà enregistrés en encaissements.
            acompte_paye = 0.0
            if acompte_le is not None and _fini(acompte) and float(acompte) > 0:
                acompte_paye = round(float(acompte), 2)
                _inserer(bind, montant=acompte_paye, mode="inconnu", date_encaissement=_jour(acompte_le),
                         reservation_id=rid, libelle=f"Location {reference} — acompte", a_qualifier=True,
                         hors_caisse=True, note_qualification="Acompte noté par sa date ; mode non enregistré.",
                         source_ancienne=f"reservation-acompte:{rid}")
            if solde_le is not None:
                prix = 0.0 if gratuite else (manuel if _fini(manuel) else (calcule if _fini(calcule) else 0.0))
                solde = round(float(prix or 0) - acompte_paye, 2)
                if solde > 0.009:
                    _inserer(bind, montant=solde, mode="inconnu", date_encaissement=_jour(solde_le),
                             reservation_id=rid, libelle=f"Location {reference} — solde", a_qualifier=True,
                             hors_caisse=True,
                             note_qualification="Solde noté par sa date ; montant reconstitué d'après le prix actuel, mode non enregistré.",
                             source_ancienne=f"reservation-solde:{rid}")


def downgrade():
    with op.batch_alter_table("caisse_mouvement") as batch:
        batch.drop_constraint("uq_caisse_mouvement_jeton", type_="unique")
        batch.drop_column("jeton")
    with op.batch_alter_table("paiement") as batch:
        batch.drop_index("ix_paiement_encaissement_id")
        batch.drop_constraint("fk_paiement_encaissement", type_="foreignkey")
        batch.drop_column("encaissement_id")
    op.drop_table("encaissement")
