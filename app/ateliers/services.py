import hashlib
import json
import sqlite3
from datetime import datetime

from app.utils.dates import utcnow
from typing import Dict, List, Tuple

from flask import current_app

from app.extensions import db
from app.models import Atelier


def _presence_db_path() -> str:
    return current_app.config.get("PRESENCE_DB_PATH")


def _inspect_presence_db(path: str) -> Tuple[bool, List[str]]:
    """Return (has_participants_table, tables) for a sqlite db path."""
    try:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        conn.close()
        return ("participants" in tables), tables
    except Exception:
        return (False, [])


def _make_uid(date_str: str, titre: str, lieu: str, horaires: str, intervenant: str) -> str:
    """UID stable : hash sur un panier de champs.

    On évite de dépendre UNIQUEMENT du titre (trop variable),
    tout en restant stable même si l'ID n'existe pas côté émargement.
    """
    blob = "|".join([(date_str or "").strip(), (titre or "").strip(), (lieu or "").strip(), (horaires or "").strip(), (intervenant or "").strip()])
    return hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()[:20].upper()


def _group_rows(rows: List[Tuple]) -> Dict[str, Dict]:
    """Regroupe les lignes participants en ateliers et calcule des stats."""
    grouped: Dict[str, Dict] = {}
    now_year = datetime.now().year

    for r in rows:
        # r schema (presence.db) :
        # id, session_date, session_name, lieu, horaires, intervenant, nom_prenom, email, ddn, sexe, type_public, ville, signature_path
        date_str = r[1]
        titre = r[2]
        lieu = r[3]
        horaires = r[4]
        intervenant = r[5]
        ddn = r[8] or ""
        sexe = (r[9] or "").strip() or "?"
        type_public = (r[10] or "").strip() or "?"
        ville = (r[11] or "").strip() or "?"

        uid = _make_uid(date_str, titre, lieu, horaires, intervenant)
        if uid not in grouped:
            grouped[uid] = {
                "atelier_uid": uid,
                "date": date_str,
                "titre": titre,
                "lieu": lieu,
                "horaires": horaires,
                "intervenant": intervenant,
                "nb": 0,
                "sexe": {},
                "type_public": {},
                "ville": {},
                "age_group": {"-18": 0, "18-25": 0, "26-60": 0, "60+": 0, "inconnu": 0},
                "creil": 0,
            }

        g = grouped[uid]
        g["nb"] += 1
        g["sexe"][sexe] = g["sexe"].get(sexe, 0) + 1
        g["type_public"][type_public] = g["type_public"].get(type_public, 0) + 1
        g["ville"][ville] = g["ville"].get(ville, 0) + 1
        if "creil" in ville.lower():
            g["creil"] += 1

        # Age group (ddn au format YYYY-MM-DD dans l'app émargement)
        try:
            y = int(ddn.split("-")[0])
            age = max(0, now_year - y)
            if age < 18:
                g["age_group"]["-18"] += 1
            elif age <= 25:
                g["age_group"]["18-25"] += 1
            elif age <= 60:
                g["age_group"]["26-60"] += 1
            else:
                g["age_group"]["60+"] += 1
        except Exception:
            g["age_group"]["inconnu"] += 1

    return grouped


def read_presence_ateliers() -> List[Dict]:
    """Lit presence.db et retourne une liste d'ateliers (dict) avec stats.

    En cas de mauvais chemin, SQLite crée une DB vide => pas de table 'participants'.
    On détecte ça et on remonte une erreur explicite.
    """
    path = _presence_db_path()
    if not path:
        raise RuntimeError('PRESENCE_DB_PATH non défini')
    has_tbl, tables = _inspect_presence_db(path)
    if not has_tbl:
        raise RuntimeError(
            f"Table 'participants' introuvable dans presence.db. Chemin utilisé: {path}. "
            f"Tables trouvées: {tables}. "
            "Astuce: vérifie que tu as copié le BON fichier presence.db dans app_gestion/instance/ "
            "(ou définis la variable d'environnement PRESENCE_DB_PATH vers le bon chemin)."
        )
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("SELECT * FROM participants")
    rows = c.fetchall()
    conn.close()

    grouped = _group_rows(rows)
    out = []
    for uid, g in grouped.items():
        total = g["nb"] or 1
        stats = {
            "sexe": g["sexe"],
            "type_public": g["type_public"],
            "ville": dict(sorted(g["ville"].items(), key=lambda kv: kv[1], reverse=True)[:10]),
            "age_group": g["age_group"],
            "creil": {"nb": g["creil"], "pct": int(round(g["creil"] / total * 100))},
        }
        out.append({
            "atelier_uid": uid,
            "date": g["date"],
            "titre": g["titre"],
            "lieu": g["lieu"],
            "horaires": g["horaires"],
            "intervenant": g["intervenant"],
            "nb_participants": g["nb"],
            "stats_json": json.dumps(stats, ensure_ascii=False),
        })

    # tri : plus récent d'abord (dd/mm/YYYY)
    def _key(a):
        try:
            return datetime.strptime(a["date"], "%d/%m/%Y")
        except Exception:
            return datetime.min
    out.sort(key=_key, reverse=True)
    return out


def sync_ateliers_from_presence_db(limit: int = 500) -> int:
    """Synchronise presence.db vers la table Atelier.

    Retourne le nombre d'ateliers créés ou mis à jour.
    """
    items = read_presence_ateliers()[:limit]
    n = 0
    now = utcnow()

    for it in items:
        uid = it["atelier_uid"]
        a = Atelier.query.filter_by(atelier_uid=uid).first()
        if not a:
            a = Atelier(
                atelier_uid=uid,
                date=it["date"],
                titre=it["titre"],
                lieu=it.get("lieu"),
                horaires=it.get("horaires"),
                intervenant=it.get("intervenant"),
            )
            db.session.add(a)

        # update snapshot
        a.nb_participants = int(it.get("nb_participants") or 0)
        a.stats_json = it.get("stats_json")
        a.date = it["date"]
        a.titre = it["titre"]
        a.lieu = it.get("lieu")
        a.horaires = it.get("horaires")
        a.intervenant = it.get("intervenant")
        a.last_sync_at = now
        n += 1

    db.session.commit()
    return n
