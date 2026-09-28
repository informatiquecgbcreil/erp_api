"""API csv standard, avec neutralisation des formules dans les cellules texte."""
from csv import *  # noqa: F403 — conserve reader, dialectes et constantes existants
import csv as _csv


import re as _re

#: Nombre simple, éventuellement négatif (« -120,50 », « -3.5 », « -1 200,00 »).
#: Mineur de l'audit : la protection anti-formule transformait les montants
#: négatifs en texte (« '-120,50 »), qui ne s'additionnaient plus dans le
#: tableur. Un nombre simple ne peut pas être une formule : il reste un nombre.
_NOMBRE = _re.compile(r"-?\d[\d\s\u00a0\u202f]*([.,]\d+)?")


def safe_cell(value):
    if isinstance(value, str) and value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")):
        if _NOMBRE.fullmatch(value.strip()):
            return value
        return "'" + value
    return value


class _Writer:
    def __init__(self, stream, *args, **kwargs):
        self._writer = _csv.writer(stream, *args, **kwargs)
        self.dialect = self._writer.dialect

    def writerow(self, row):
        return self._writer.writerow([safe_cell(cell) for cell in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


writer = _Writer


class DictWriter(_csv.DictWriter):
    def __init__(self, f, fieldnames, restval="", extrasaction="raise", dialect="excel", *args, **kwargs):
        super().__init__(f, fieldnames, restval, extrasaction, dialect, *args, **kwargs)
        self.writer = writer(f, dialect, *args, **kwargs)
