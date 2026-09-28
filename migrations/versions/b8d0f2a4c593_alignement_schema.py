"""Alignement du schéma sur le modèle (mineur « base » de l'audit).

Mesuré sur une base PostgreSQL 16 migrée de zéro : 130 écarts entre le
modèle et le schéma. La plupart sont des index de même rôle sous un autre
nom (sans effet). Restaient 7 clés étrangères et 40 index déclarés au modèle
mais jamais créés par les migrations. Cette migration les crée, sans jamais
modifier une donnée :

- un index n'est créé que si aucun index ou contrainte ne couvre déjà
  exactement les mêmes colonnes ; un index unique n'est créé unique que si
  les données le permettent (sinon index simple, signalé) ;
- une clé étrangère (PostgreSQL seulement : SQLite ne sait pas l'ajouter)
  n'est créée que si aucune ligne n'y contrevient ; sinon elle est sautée et
  signalée — les lignes orphelines restent, à corriger à la main.

Les autres écarts (nullabilité de 12 colonnes, un type, une colonne
historique en trop) sont documentés, pas modifiés : les corriger pourrait
refuser des données existantes.
"""
import sqlalchemy as sa
from alembic import op

revision = "b8d0f2a4c593"
down_revision = "a7c9e1f3b482"
branch_labels = None
depends_on = None

INDEX = [
    ("agenda_preference", "ix_agenda_preference_user_id", ['user_id'], True),
    ("budget_categorie_referentiel", "ix_budget_categorie_referentiel_actif", ['actif'], False),
    ("budget_categorie_referentiel", "ix_budget_categorie_referentiel_compte_id", ['compte_id'], False),
    ("budget_categorie_referentiel", "ix_budget_categorie_referentiel_nature", ['nature'], False),
    ("budget_categorie_referentiel", "ix_budget_categorie_referentiel_secteur", ['secteur'], False),
    ("budget_compte_referentiel", "ix_budget_compte_referentiel_actif", ['actif'], False),
    ("budget_compte_referentiel", "ix_budget_compte_referentiel_code", ['code'], False),
    ("budget_compte_referentiel", "ix_budget_compte_referentiel_nature", ['nature'], False),
    ("budget_compte_referentiel", "ix_budget_compte_referentiel_secteur", ['secteur'], False),
    ("budget_modele_ligne_referentiel", "ix_budget_modele_ligne_referentiel_categorie_id", ['categorie_id'], False),
    ("budget_modele_ligne_referentiel", "ix_budget_modele_ligne_referentiel_modele_id", ['modele_id'], False),
    ("budget_modele_referentiel", "ix_budget_modele_referentiel_actif", ['actif'], False),
    ("budget_modele_referentiel", "ix_budget_modele_referentiel_secteur", ['secteur'], False),
    ("depense_affectation", "ix_depense_affectation_depense_id", ['depense_id'], False),
    ("depense_affectation", "ix_depense_affectation_ligne_budget_id", ['ligne_budget_id'], False),
    ("depense_affectation", "ix_depense_affectation_subvention_id", ['subvention_id'], False),
    ("framework", "ix_framework_code", ['code'], True),
    ("glossaire_terme", "ix_glossaire_terme_terme", ['terme'], True),
    ("insertion_diplome_ref", "ix_insertion_diplome_ref_label", ['label'], True),
    ("insertion_dispositif_ref", "ix_insertion_dispositif_ref_label", ['label'], True),
    ("insertion_niveau_ref", "ix_insertion_niveau_ref_label", ['label'], True),
    ("insertion_prescripteur_ref", "ix_insertion_prescripteur_ref_label", ['label'], True),
    ("insertion_titre_sejour_type_ref", "ix_insertion_titre_sejour_type_ref_label", ['label'], True),
    ("materiel_consommation_config", "ix_materiel_consommation_config_actif", ['actif'], False),
    ("materiel_consommation_config", "ix_materiel_consommation_config_date_debut", ['date_debut'], False),
    ("materiel_consommation_config", "ix_materiel_consommation_config_date_fin", ['date_fin'], False),
    ("materiel_consommation_ligne", "ix_materiel_consommation_ligne_config_id", ['config_id'], False),
    ("materiel_consommation_ligne", "ix_materiel_consommation_ligne_materiel_id", ['materiel_id'], False),
    ("notification_reglage", "ix_notification_reglage_code", ['code'], True),
    ("portail_attempt", "ix_portail_attempt_attempt_id", ['attempt_id'], True),
    ("presence_materiel_consommation", "ix_presence_materiel_consommation_materiel_id", ['materiel_id'], False),
    ("presence_materiel_consommation", "ix_presence_materiel_consommation_participant_id", ['participant_id'], False),
    ("presence_materiel_consommation", "ix_presence_materiel_consommation_presence_id", ['presence_id'], False),
    ("presence_materiel_consommation", "ix_presence_materiel_consommation_session_id", ['session_id'], False),
    ("projet_indicateur_valeur", "ix_projet_indicateur_valeur_date_releve", ['date_releve'], False),
    ("reservation", "ix_reservation_avoir_numero", ['avoir_numero'], True),
    ("session_activite", "ix_session_activite_consommation_config_id", ['consommation_config_id'], False),
    ("session_materiel", "ix_session_materiel_materiel_id", ['materiel_id'], False),
    ("session_materiel", "ix_session_materiel_session_id", ['session_id'], False),
    ("transition_thematique", "ix_transition_thematique_code", ['code'], True),
]

#: (table, colonne, table cible, colonne cible, ON DELETE)
CLES = [
    ("atelier_activite", "continuity_parent_id", "atelier_activite", "id", None),
    ("learning_project", "created_by_id", "user", "id", None),
    ("learning_project", "framework_id_default", "framework", "id", None),
    ("orientation_acces_droit", "quartier_id", "quartier", "id", "SET NULL"),
    ("questionnaire", "projet_id", "projet", "id", "SET NULL"),
    ("session_assessment", "assessed_by_id", "user", "id", None),
    ("skill", "framework_id", "framework", "id", None),
]


def _deja_couvert(inspecteur, table, colonnes):
    existants = [i["column_names"] for i in inspecteur.get_indexes(table)]
    existants += [u["column_names"] for u in inspecteur.get_unique_constraints(table)]
    pk = inspecteur.get_pk_constraint(table).get("constrained_columns") or []
    existants.append(pk)
    return any(list(e) == list(colonnes) for e in existants)


#: Ce que cette migration a réellement créé : son retour arrière ne défait
#: que cela (et jamais un index ou une clé antérieurs de même rôle).
TRACE = "alignement_schema_cree"


def _noter(bind, nom, table, genre):
    bind.execute(sa.text(f"INSERT INTO {TRACE} (nom, nom_table, genre) VALUES (:n, :t, :g)"),
                 {"n": nom, "t": table, "g": genre})


def upgrade():
    bind = op.get_bind()
    inspecteur = sa.inspect(bind)
    tables = set(inspecteur.get_table_names())
    preparer = bind.dialect.identifier_preparer
    if TRACE not in tables:
        op.create_table(TRACE, sa.Column("nom", sa.String(80), primary_key=True),
                        sa.Column("nom_table", sa.String(80), nullable=False),
                        sa.Column("genre", sa.String(10), nullable=False))
    for table, nom, colonnes, unique in INDEX:
        if table not in tables:
            continue
        noms_colonnes = {c["name"] for c in inspecteur.get_columns(table)}
        if not set(colonnes) <= noms_colonnes or _deja_couvert(inspecteur, table, colonnes):
            continue
        if unique:
            liste = ", ".join(preparer.quote(c) for c in colonnes)
            doublons = bind.execute(sa.text(
                f"SELECT 1 FROM {preparer.quote(table)} WHERE {' AND '.join(preparer.quote(c) + ' IS NOT NULL' for c in colonnes)} "
                f"GROUP BY {liste} HAVING count(*) > 1 LIMIT 1")).first()
            if doublons:
                print(f"[alignement] {table}.{colonnes} : doublons présents, index simple au lieu d'unique.")
                unique = False
        op.create_index(nom, table, colonnes, unique=unique)
        _noter(bind, nom, table, "index")
    if bind.dialect.name != "postgresql":
        return
    for table, colonne, cible, colonne_cible, suppression in CLES:
        if table not in tables or cible not in tables:
            continue
        if any(fk["constrained_columns"] == [colonne] for fk in inspecteur.get_foreign_keys(table)):
            continue
        orphelins = bind.execute(sa.text(
            f"SELECT 1 FROM {preparer.quote(table)} t WHERE t.{preparer.quote(colonne)} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {preparer.quote(cible)} c WHERE c.{preparer.quote(colonne_cible)} = t.{preparer.quote(colonne)}) "
            f"LIMIT 1")).first()
        if orphelins:
            print(f"[alignement] {table}.{colonne} : lignes orphelines, clé étrangère non créée.")
            continue
        op.create_foreign_key(f"fk_{table}_{colonne}"[:63], table, cible, [colonne], [colonne_cible],
                              ondelete=suppression)
        _noter(bind, f"fk_{table}_{colonne}"[:63], table, "cle")


def downgrade():
    bind = op.get_bind()
    if TRACE not in set(sa.inspect(bind).get_table_names()):
        return
    crees = bind.execute(sa.text(f"SELECT nom, nom_table, genre FROM {TRACE}")).fetchall()
    for nom, table, genre in crees:
        if genre == "cle":
            op.drop_constraint(nom, table, type_="foreignkey")
    for nom, table, genre in crees:
        if genre == "index":
            op.drop_index(nom, table_name=table)
    op.drop_table(TRACE)
