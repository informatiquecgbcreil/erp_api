"""insertion module foundation

Revision ID: 21e2f3a4b5c6
Revises: 20d1e2f3a4b5
Create Date: 2026-04-02 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime, timezone, date

from alembic import op
import sqlalchemy as sa


revision = "21e2f3a4b5c6"
down_revision = "20d1e2f3a4b5"
branch_labels = None
depends_on = None


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _resolve_is_active(date_entree, date_sortie):
    today = date.today()
    if date_entree and date_entree > today:
        return False
    if date_sortie and date_sortie < today:
        return False
    return True


def upgrade():
    op.create_table(
        "insertion_dispositif_ref",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("label", name="uq_insertion_dispositif_ref_label"),
    )
    op.create_index("ix_insertion_dispositif_ref_label", "insertion_dispositif_ref", ["label"], unique=False)

    op.create_table(
        "insertion_prescripteur_ref",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("label", name="uq_insertion_prescripteur_ref_label"),
    )
    op.create_index("ix_insertion_prescripteur_ref_label", "insertion_prescripteur_ref", ["label"], unique=False)

    op.create_table(
        "insertion_titre_sejour_type_ref",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("label", name="uq_insertion_titre_sejour_type_ref_label"),
    )
    op.create_index("ix_insertion_titre_sejour_type_ref_label", "insertion_titre_sejour_type_ref", ["label"], unique=False)

    op.create_table(
        "insertion_diplome_ref",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("label", name="uq_insertion_diplome_ref_label"),
    )
    op.create_index("ix_insertion_diplome_ref_label", "insertion_diplome_ref", ["label"], unique=False)

    op.create_table(
        "insertion_niveau_ref",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("label", name="uq_insertion_niveau_ref_label"),
    )
    op.create_index("ix_insertion_niveau_ref_label", "insertion_niveau_ref", ["label"], unique=False)

    op.create_table(
        "participant_insertion_profile",
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("pays_origine", sa.String(length=120), nullable=True),
        sa.Column("cir_obtenu", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )

    op.create_table(
        "participant_insertion_parcours",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dispositif_id", sa.Integer(), sa.ForeignKey("insertion_dispositif_ref.id"), nullable=True),
        sa.Column("prescripteur_id", sa.Integer(), sa.ForeignKey("insertion_prescripteur_ref.id"), nullable=True),
        sa.Column("titre_sejour_type_id", sa.Integer(), sa.ForeignKey("insertion_titre_sejour_type_ref.id"), nullable=True),
        sa.Column("date_entree", sa.Date(), nullable=True),
        sa.Column("date_sortie", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("legacy_source", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )
    op.create_index("ix_participant_insertion_parcours_participant_id", "participant_insertion_parcours", ["participant_id"], unique=False)
    op.create_index("ix_participant_insertion_parcours_legacy_source", "participant_insertion_parcours", ["legacy_source"], unique=False)

    op.create_table(
        "participant_insertion_certification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("diplome_id", sa.Integer(), sa.ForeignKey("insertion_diplome_ref.id"), nullable=True),
        sa.Column("niveau_id", sa.Integer(), sa.ForeignKey("insertion_niveau_ref.id"), nullable=True),
        sa.Column("date_obtention", sa.Date(), nullable=True),
        sa.Column("commentaire", sa.Text(), nullable=True),
        sa.Column("legacy_source", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )
    op.create_index("ix_participant_insertion_certification_participant_id", "participant_insertion_certification", ["participant_id"], unique=False)
    op.create_index("ix_participant_insertion_certification_legacy_source", "participant_insertion_certification", ["legacy_source"], unique=False)

    bind = op.get_bind()
    meta = sa.MetaData()
    participant = sa.Table("participant", meta, autoload_with=bind)
    profile = sa.Table("participant_insertion_profile", meta, autoload_with=bind)
    parcours = sa.Table("participant_insertion_parcours", meta, autoload_with=bind)
    certification = sa.Table("participant_insertion_certification", meta, autoload_with=bind)
    titre_ref = sa.Table("insertion_titre_sejour_type_ref", meta, autoload_with=bind)
    diplome_ref = sa.Table("insertion_diplome_ref", meta, autoload_with=bind)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def get_or_create(table, label):
        cleaned = _clean(label)
        if not cleaned:
            return None
        existing = bind.execute(sa.select(table.c.id).where(sa.func.lower(table.c.label) == cleaned.lower())).scalar()
        if existing:
            return existing
        res = bind.execute(
            table.insert().values(
                label=cleaned,
                code=None,
                is_active=True,
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
        )
        return res.inserted_primary_key[0]

    rows = bind.execute(
        sa.select(
            participant.c.id,
            participant.c.pays_origine,
            participant.c.cir_obtenu,
            participant.c.titre_sejour_type,
            participant.c.date_entree_dispositif,
            participant.c.date_sortie_dispositif,
            participant.c.diplome_obtenu,
            participant.c.created_by_user_id,
        )
    ).mappings().all()

    for row in rows:
        pays_origine = _clean(row["pays_origine"])
        cir_obtenu = row["cir_obtenu"]
        titre = _clean(row["titre_sejour_type"])
        date_entree = row["date_entree_dispositif"]
        date_sortie = row["date_sortie_dispositif"]
        diplome = _clean(row["diplome_obtenu"])
        actor_id = row["created_by_user_id"]

        if pays_origine or cir_obtenu is not None:
            bind.execute(
                profile.insert().values(
                    participant_id=row["id"],
                    pays_origine=pays_origine,
                    cir_obtenu=cir_obtenu,
                    created_at=now,
                    updated_at=now,
                    created_by_user_id=actor_id,
                    updated_by_user_id=actor_id,
                )
            )

        titre_id = get_or_create(titre_ref, titre)
        if titre_id or date_entree or date_sortie:
            bind.execute(
                parcours.insert().values(
                    participant_id=row["id"],
                    dispositif_id=None,
                    prescripteur_id=None,
                    titre_sejour_type_id=titre_id,
                    date_entree=date_entree,
                    date_sortie=date_sortie,
                    is_active=_resolve_is_active(date_entree, date_sortie),
                    legacy_source=True,
                    created_at=now,
                    updated_at=now,
                    created_by_user_id=actor_id,
                    updated_by_user_id=actor_id,
                )
            )

        diplome_id = get_or_create(diplome_ref, diplome)
        if diplome_id:
            bind.execute(
                certification.insert().values(
                    participant_id=row["id"],
                    diplome_id=diplome_id,
                    niveau_id=None,
                    date_obtention=None,
                    commentaire=None,
                    legacy_source=True,
                    created_at=now,
                    updated_at=now,
                    created_by_user_id=actor_id,
                    updated_by_user_id=actor_id,
                )
            )


def downgrade():
    op.drop_index("ix_participant_insertion_certification_legacy_source", table_name="participant_insertion_certification")
    op.drop_index("ix_participant_insertion_certification_participant_id", table_name="participant_insertion_certification")
    op.drop_table("participant_insertion_certification")

    op.drop_index("ix_participant_insertion_parcours_legacy_source", table_name="participant_insertion_parcours")
    op.drop_index("ix_participant_insertion_parcours_participant_id", table_name="participant_insertion_parcours")
    op.drop_table("participant_insertion_parcours")

    op.drop_table("participant_insertion_profile")

    op.drop_index("ix_insertion_niveau_ref_label", table_name="insertion_niveau_ref")
    op.drop_table("insertion_niveau_ref")

    op.drop_index("ix_insertion_diplome_ref_label", table_name="insertion_diplome_ref")
    op.drop_table("insertion_diplome_ref")

    op.drop_index("ix_insertion_titre_sejour_type_ref_label", table_name="insertion_titre_sejour_type_ref")
    op.drop_table("insertion_titre_sejour_type_ref")

    op.drop_index("ix_insertion_prescripteur_ref_label", table_name="insertion_prescripteur_ref")
    op.drop_table("insertion_prescripteur_ref")

    op.drop_index("ix_insertion_dispositif_ref_label", table_name="insertion_dispositif_ref")
    op.drop_table("insertion_dispositif_ref")
