"""Espace salarié : intégration de l'application « Récup ».

Heures supplémentaires et récupérations (circuit signé), frais
kilométriques et barèmes, profils salariaux confidentiels, coffre-fort de
documents. Tout est rattaché à la fiche ``salarie`` ; ``salarie.user_id``
relie la fiche au compte de connexion.

L'ancienne synchronisation par API (table ``recup_rh_snapshot``, colonnes
``salarie.recup_*``) devient inutile : Récup n'a jamais tourné en
production. La table d'instantanés n'est supprimée QUE si elle est vide
(c'était un cache, jamais une source) ; les colonnes ``recup_*`` restent en
base, sans usage, pour ne toucher à aucune donnée existante.

Rejouable : chaque création vérifie d'abord l'existence.
"""
import sqlalchemy as sa
from alembic import op

revision = "a9d4e6f8b2c1"
down_revision = "e5f7a9b1c235"
branch_labels = None
depends_on = None


def _index(insp, table, nom, colonnes, unique=False):
    existants = {i["name"] for i in insp.get_indexes(table)}
    if nom not in existants:
        op.create_index(nom, table, colonnes, unique=unique)


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("salarie"):
        cols = {c["name"] for c in insp.get_columns("salarie")}
        if "user_id" not in cols:
            with op.batch_alter_table("salarie") as batch:
                batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
                batch.create_foreign_key("fk_salarie_user_id", "user", ["user_id"], ["id"], ondelete="SET NULL")
        _index(sa.inspect(bind), "salarie", "ix_salarie_user_id", ["user_id"], unique=True)

    if not insp.has_table("signature_rh"):
        op.create_table(
            "signature_rh",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("signataire_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("signataire_nom", sa.String(160), nullable=False, server_default=""),
            sa.Column("contexte", sa.String(60), nullable=False),
            sa.Column("objet_type", sa.String(40), nullable=False),
            sa.Column("objet_id", sa.Integer(), nullable=False),
            sa.Column("chemin", sa.String(500), nullable=True),
            sa.Column("sha256", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    insp = sa.inspect(bind)
    _index(insp, "signature_rh", "ix_signature_rh_signataire_user_id", ["signataire_user_id"])
    _index(insp, "signature_rh", "ix_signature_rh_objet_id", ["objet_id"])

    if not insp.has_table("heure_supplementaire"):
        op.create_table(
            "heure_supplementaire",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("salarie_id", sa.Integer(), sa.ForeignKey("salarie.id"), nullable=False),
            sa.Column("saisi_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("date_travail", sa.Date(), nullable=False),
            sa.Column("minutes", sa.Integer(), nullable=False),
            sa.Column("motif", sa.Text(), nullable=False, server_default=""),
            sa.Column("est_ajustement", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("origine_id", sa.Integer(), sa.ForeignKey("heure_supplementaire.id"), nullable=True),
            sa.Column("commentaire_direction", sa.Text(), nullable=True),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("session_activite.id", ondelete="SET NULL"), nullable=True),
            sa.Column("creneau_id", sa.Integer(), sa.ForeignKey("agenda_creneau.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    insp = sa.inspect(bind)
    for col in ("salarie_id", "date_travail", "origine_id", "session_id", "creneau_id"):
        _index(insp, "heure_supplementaire", f"ix_heure_supplementaire_{col}", [col])

    if not insp.has_table("demande_recuperation"):
        op.create_table(
            "demande_recuperation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("salarie_id", sa.Integer(), sa.ForeignKey("salarie.id"), nullable=False),
            sa.Column("demandeur_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("date_recuperation", sa.Date(), nullable=False),
            sa.Column("minutes", sa.Integer(), nullable=False),
            sa.Column("motif", sa.Text(), nullable=False, server_default=""),
            sa.Column("statut", sa.String(20), nullable=False, server_default="brouillon"),
            sa.Column("commentaire_direction", sa.Text(), nullable=True),
            sa.Column("transmise_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("transmise_le", sa.DateTime(), nullable=True),
            sa.Column("decidee_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("decidee_le", sa.DateTime(), nullable=True),
            sa.Column("notifiee_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("notifiee_le", sa.DateTime(), nullable=True),
            sa.Column("signature_salarie_id", sa.Integer(), sa.ForeignKey("signature_rh.id", ondelete="SET NULL"), nullable=True),
            sa.Column("signature_transmission_id", sa.Integer(), sa.ForeignKey("signature_rh.id", ondelete="SET NULL"), nullable=True),
            sa.Column("signature_decision_id", sa.Integer(), sa.ForeignKey("signature_rh.id", ondelete="SET NULL"), nullable=True),
            sa.Column("signature_notification_id", sa.Integer(), sa.ForeignKey("signature_rh.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    insp = sa.inspect(bind)
    for col in ("salarie_id", "date_recuperation", "statut"):
        _index(insp, "demande_recuperation", f"ix_demande_recuperation_{col}", [col])

    if not insp.has_table("bareme_kilometrique"):
        op.create_table(
            "bareme_kilometrique",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("annee", sa.Integer(), nullable=False),
            sa.Column("type_vehicule", sa.String(30), nullable=False),
            sa.Column("puissance_fiscale", sa.Integer(), nullable=False),
            sa.Column("km_de", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("km_a", sa.Integer(), nullable=True),
            sa.Column("taux_millieme", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("forfait_centimes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("bonus_electrique_pct", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("annee", "type_vehicule", "puissance_fiscale", "km_de", "km_a",
                                name="uq_bareme_kilometrique"),
        )
    insp = sa.inspect(bind)
    _index(insp, "bareme_kilometrique", "ix_bareme_kilometrique_annee", ["annee"])

    if not insp.has_table("frais_kilometrique"):
        op.create_table(
            "frais_kilometrique",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("salarie_id", sa.Integer(), sa.ForeignKey("salarie.id"), nullable=False),
            sa.Column("saisi_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("date_trajet", sa.Date(), nullable=False),
            sa.Column("annee", sa.Integer(), nullable=False),
            sa.Column("annee_bareme", sa.Integer(), nullable=False),
            sa.Column("type_vehicule", sa.String(30), nullable=False),
            sa.Column("puissance_fiscale", sa.Integer(), nullable=False),
            sa.Column("electrique", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("distance_km", sa.Integer(), nullable=False),
            sa.Column("cumul_km_avant", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("motif", sa.String(500), nullable=False),
            sa.Column("montant_centimes", sa.Integer(), nullable=False),
            sa.Column("taux_millieme", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("forfait_centimes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("bonus_electrique_pct", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("justificatif_chemin", sa.String(500), nullable=True),
            sa.Column("justificatif_nom", sa.String(300), nullable=True),
            sa.Column("statut", sa.String(20), nullable=False, server_default="signee"),
            sa.Column("signee_le", sa.DateTime(), nullable=True),
            sa.Column("signature_id", sa.Integer(), sa.ForeignKey("signature_rh.id", ondelete="SET NULL"), nullable=True),
            sa.Column("session_id", sa.Integer(), sa.ForeignKey("session_activite.id", ondelete="SET NULL"), nullable=True),
            sa.Column("depense_id", sa.Integer(), sa.ForeignKey("depense.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    insp = sa.inspect(bind)
    for col in ("salarie_id", "date_trajet", "annee", "session_id", "depense_id"):
        _index(insp, "frais_kilometrique", f"ix_frais_kilometrique_{col}", [col])

    if not insp.has_table("profil_salarial"):
        op.create_table(
            "profil_salarial",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("salarie_id", sa.Integer(), sa.ForeignKey("salarie.id"), nullable=False),
            sa.Column("taux_horaire_brut_centimes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("taux_horaire_charge_centimes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("semaines_travaillees", sa.Integer(), nullable=False, server_default="46"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        )
    insp = sa.inspect(bind)
    _index(insp, "profil_salarial", "ix_profil_salarial_salarie_id", ["salarie_id"], unique=True)

    if not insp.has_table("profil_salarial_charge"):
        op.create_table(
            "profil_salarial_charge",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profil_id", sa.Integer(), sa.ForeignKey("profil_salarial.id", ondelete="CASCADE"), nullable=False),
            sa.Column("libelle", sa.String(120), nullable=False),
            sa.Column("centimes_par_heure", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("note", sa.String(500), nullable=True),
        )
    insp = sa.inspect(bind)
    _index(insp, "profil_salarial_charge", "ix_profil_salarial_charge_profil_id", ["profil_id"])

    if not insp.has_table("type_document_rh"):
        op.create_table(
            "type_document_rh",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(50), nullable=False, unique=True),
            sa.Column("libelle", sa.String(120), nullable=False),
            sa.Column("extensions", sa.String(300), nullable=False, server_default="pdf,jpg,png,docx,xlsx"),
        )

    insp = sa.inspect(bind)
    if not insp.has_table("document_rh"):
        op.create_table(
            "document_rh",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("depose_par_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("type_id", sa.Integer(), sa.ForeignKey("type_document_rh.id"), nullable=False),
            sa.Column("salarie_id", sa.Integer(), sa.ForeignKey("salarie.id"), nullable=True),
            sa.Column("nom_original", sa.String(300), nullable=False),
            sa.Column("chemin", sa.String(500), nullable=False),
            sa.Column("taille", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mime", sa.String(120), nullable=True),
            sa.Column("note", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    insp = sa.inspect(bind)
    for col in ("depose_par_user_id", "type_id", "salarie_id"):
        _index(insp, "document_rh", f"ix_document_rh_{col}", [col])

    if not insp.has_table("document_rh_acces"):
        op.create_table(
            "document_rh_acces",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("document_id", sa.Integer(), sa.ForeignKey("document_rh.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("document_id", "user_id", name="uq_document_rh_acces"),
        )
    insp = sa.inspect(bind)
    _index(insp, "document_rh_acces", "ix_document_rh_acces_document_id", ["document_id"])
    _index(insp, "document_rh_acces", "ix_document_rh_acces_user_id", ["user_id"])

    # Types de documents usuels, pour que le coffre-fort serve dès le premier jour.
    if not bind.execute(sa.text("SELECT COUNT(*) FROM type_document_rh")).scalar():
        types = sa.table(
            "type_document_rh",
            sa.column("code", sa.String), sa.column("libelle", sa.String), sa.column("extensions", sa.String),
        )
        op.bulk_insert(types, [
            {"code": "fiche_paie", "libelle": "Fiche de paie", "extensions": "pdf"},
            {"code": "contrat", "libelle": "Contrat et avenants", "extensions": "pdf,docx"},
            {"code": "attestation", "libelle": "Attestation", "extensions": "pdf,jpg,png"},
            {"code": "justificatif", "libelle": "Justificatif", "extensions": "pdf,jpg,jpeg,png"},
            {"code": "autre", "libelle": "Autre document", "extensions": "pdf,jpg,jpeg,png,docx,xlsx,odt,ods"},
        ])

    # Ancien cache de la synchronisation Récup → ERP : supprimé seulement vide.
    if insp.has_table("recup_rh_snapshot"):
        restant = bind.execute(sa.text("SELECT COUNT(*) FROM recup_rh_snapshot")).scalar()
        if not restant:
            op.drop_table("recup_rh_snapshot")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ("document_rh_acces", "document_rh", "type_document_rh", "profil_salarial_charge",
                  "profil_salarial", "frais_kilometrique", "bareme_kilometrique",
                  "demande_recuperation", "heure_supplementaire", "signature_rh"):
        if insp.has_table(table):
            op.drop_table(table)
    cols = {c["name"] for c in insp.get_columns("salarie")}
    if "user_id" in cols:
        with op.batch_alter_table("salarie") as batch:
            batch.drop_column("user_id")
