"""Réservation atomique d'un numéro dans la transaction du document."""
from sqlalchemy import case
from app.extensions import db
from app.models import FinancialSequence


def next_number(namespace, existing_numbers):
    maximum = 0
    for value in existing_numbers:
        try:
            maximum = max(maximum, int(str(value).rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = FinancialSequence.__table__
    statement = insert(table).values(namespace=namespace, value=maximum + 1)
    statement = statement.on_conflict_do_update(index_elements=[table.c.namespace], set_={
        "value": case((table.c.value < maximum, maximum + 1), else_=table.c.value + 1)
    }).returning(table.c.value)
    return db.session.execute(statement).scalar_one()
