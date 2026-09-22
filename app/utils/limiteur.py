"""Limiteur de débit en mémoire, sans dépendance.

Sert aux rares pages publiques (kiosque d'émargement) où une personne
malveillante pourrait essayer en boucle : 10 000 codes PIN à 4 chiffres
s'épuisent en quelques minutes sans frein, et la recherche de noms
permettrait sinon d'aspirer tout l'annuaire des participants.

Portée : un seul processus (waitress sert l'application dans un processus
multi-thread). Les compteurs repartent de zéro au redémarrage, ce qui est
acceptable pour un frein anti-automate.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class Limiteur:
    def __init__(self, maximum: int, fenetre_secondes: float, capacite_cles: int = 5000):
        self.maximum = int(maximum)
        self.fenetre = float(fenetre_secondes)
        self.capacite = int(capacite_cles)
        self._evenements: dict[str, deque] = {}
        self._verrou = threading.Lock()

    def _purger(self, file: deque, maintenant: float) -> None:
        limite = maintenant - self.fenetre
        while file and file[0] <= limite:
            file.popleft()

    def depasse(self, cle: str) -> bool:
        """Vrai si la clé a déjà atteint le maximum dans la fenêtre."""
        maintenant = time.monotonic()
        with self._verrou:
            file = self._evenements.get(cle)
            if not file:
                return False
            self._purger(file, maintenant)
            return len(file) >= self.maximum

    def noter(self, cle: str) -> None:
        maintenant = time.monotonic()
        with self._verrou:
            if cle not in self._evenements and len(self._evenements) >= self.capacite:
                # Mémoire bornée : on oublie les clés les plus anciennes.
                for ancienne in list(self._evenements)[: self.capacite // 10 or 1]:
                    self._evenements.pop(ancienne, None)
            file = self._evenements.setdefault(cle, deque())
            self._purger(file, maintenant)
            file.append(maintenant)

    def autoriser(self, cle: str) -> bool:
        """Dit si l'essai est permis, et ne compte que les essais permis."""
        if self.depasse(cle):
            return False
        self.noter(cle)
        return True

    def reinitialiser(self) -> None:
        with self._verrou:
            self._evenements.clear()
