import os
from sqlalchemy import create_engine, MetaData, Table, inspect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Source SQLite
SQLITE_PATH = os.path.join(BASE_DIR, "instance", "database.db")
SQLITE_URL = f"sqlite:///{SQLITE_PATH.replace('\\', '/')}"

# --- Cible Postgres
POSTGRES_URL = os.environ.get("DATABASE_URL")
if not POSTGRES_URL:
    raise RuntimeError("DATABASE_URL n'est pas défini (PostgreSQL)")

sqlite_engine = create_engine(SQLITE_URL)
pg_engine = create_engine(POSTGRES_URL)

sqlite_meta = MetaData()
pg_meta = MetaData()

print("🔍 Lecture des tables SQLite...")
sqlite_meta.reflect(bind=sqlite_engine)
sqlite_tables = set(sqlite_meta.tables.keys())
print(f"📦 {len(sqlite_tables)} tables trouvées (SQLite)")

pg_insp = inspect(pg_engine)
pg_tables = set(pg_insp.get_table_names(schema="public"))
print(f"🧱 {len(pg_tables)} tables trouvées (Postgres/public)")

if len(pg_tables) == 0:
    raise RuntimeError(
        "Postgres ne contient AUCUNE table dans le schéma public.\n"
        "➡️ Lance d'abord l'app en Postgres (db.create_all) pour créer le schéma,\n"
        "puis relance la migration."
    )

# -------------------------------------------------------------------
# ORDRE DE MIGRATION (corrigé pour tes FK)
# -------------------------------------------------------------------
# Parents d'abord : user, référentiels, atelier/session/projet, puis enfants.
PREFERRED_ORDER = [
    # Auth / users
    "user",

    # Référentiels
    "quartier",
    "referentiel",
    "competence",

    # Ateliers & activités (parents)
    "atelier",
    "atelier_activite",
    "session_activite",

    # Projets (parent)
    "projet",
    "periode_financement",
    "subvention",

    # Participants & présences
    "participant",
    "presence_activite",

    # Pédagogie : objectifs (dépend de projet_id / atelier_id / session_id)
    "objectif",

    # Tables de liaison (souvent dépendantes)
    "atelier_competence",
    "objectif_competence",
    "session_competence",

    # Liens projet (dépend de projet)
    "subvention_projet",
    "projet_atelier",
    "projet_competence",
    "projet_indicateur",

    # Finance (souvent dépend de projet/subvention/ligne)
    "ligne_budget",
    "depense",
    "depense_document",

    # Inventaire / factures
    "facture_achat",
    "facture_ligne",
    "inventaire_item",

    # Evaluations / archives (dépend atelier/session)
    "evaluation",
    "archive_emargement",

    # Capacités / compléments
    "atelier_capacite_mois",
]

# Garde uniquement ce qui existe des deux côtés
ordered_tables = [t for t in PREFERRED_ORDER if t in sqlite_tables and t in pg_tables]
remaining = sorted((sqlite_tables & pg_tables) - set(ordered_tables))
ordered_tables.extend(remaining)

print("🧭 Ordre de migration:")
print("   " + " -> ".join(ordered_tables))

def fetch_rows(sqlite_conn, table: Table):
    res = sqlite_conn.execute(table.select())
    return res.mappings().all()

def insert_rows(pg_conn, table_name: str, rows):
    if not rows:
        print("   (vide)")
        return
    pg_table = Table(table_name, pg_meta, schema="public", autoload_with=pg_engine)
    pg_conn.execute(pg_table.insert(), rows)
    print(f"   ✅ {len(rows)} ligne(s)")

with sqlite_engine.connect() as sqlite_conn, pg_engine.connect() as pg_conn:
    trans = pg_conn.begin()
    try:
        for tname in ordered_tables:
            print(f"➡️ Migration table : {tname}")
            sqlite_table = sqlite_meta.tables[tname]
            rows = fetch_rows(sqlite_conn, sqlite_table)
            insert_rows(pg_conn, tname, rows)

        trans.commit()
        print("✅ Migration terminée avec succès.")
    except Exception:
        trans.rollback()
        print("❌ Migration annulée (rollback) suite à une erreur.")
        raise
