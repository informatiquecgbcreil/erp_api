"""Import de compétences (DigComp/Pix/CléA...) depuis un CSV.

But : éviter la saisie à la main.

Usage (Windows PowerShell) :
  .\.venv\Scripts\python.exe tools\import_skills_csv.py --framework DIGCOMP_2_2 --name "DigComp" --version "2.2" --csv data\skills_template.csv

Colonnes CSV attendues :
  code,label,description,domain_code,domain_label,sort_order,actif

Notes :
 - Si le framework n'existe pas, il est créé.
 - Si une compétence (framework_id + code) existe, elle est mise à jour.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import Framework, Skill


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--framework", required=True, help="Code du référentiel (ex: DIGCOMP_2_2)")
    p.add_argument("--name", required=True, help="Nom lisible (ex: DigComp)")
    p.add_argument("--version", default=None, help="Version (ex: 2.2)")
    p.add_argument("--lang", default="fr", help="Langue (fr/en)")
    p.add_argument("--source_url", default=None)
    p.add_argument("--csv", required=True, help="Chemin vers le CSV")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV introuvable: {csv_path}")

    app = create_app()
    with app.app_context():
        fw = Framework.query.filter_by(code=args.framework).first()
        if not fw:
            fw = Framework(code=args.framework, nom=args.name, version=args.version, lang=args.lang, source_url=args.source_url)
            db.session.add(fw)
            db.session.flush()  # obtenir fw.id

        updated = 0
        created = 0
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            required = {"code", "label"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise SystemExit(f"CSV: colonnes manquantes: {sorted(missing)}")

            for row in reader:
                code = (row.get("code") or "").strip()
                if not code:
                    continue

                sk = Skill.query.filter_by(framework_id=fw.id, code=code).first()
                if not sk:
                    sk = Skill(framework_id=fw.id, code=code, label=row.get("label", "").strip() or code)
                    db.session.add(sk)
                    created += 1
                else:
                    updated += 1

                sk.label = row.get("label", "").strip() or sk.label
                sk.description = (row.get("description") or "").strip() or None
                sk.domain_code = (row.get("domain_code") or "").strip() or None
                sk.domain_label = (row.get("domain_label") or "").strip() or None
                sk.sort_order = int(row["sort_order"]) if (row.get("sort_order") or "").strip().isdigit() else sk.sort_order
                actif_raw = (row.get("actif") or "").strip().lower()
                if actif_raw in {"0", "false", "non", "n"}:
                    sk.actif = False
                elif actif_raw in {"1", "true", "oui", "o", "y", "yes"}:
                    sk.actif = True

        db.session.commit()
        print(f"Framework: {fw.code} (id={fw.id})")
        print(f"Compétences créées: {created}")
        print(f"Compétences mises à jour: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
