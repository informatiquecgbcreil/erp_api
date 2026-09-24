"""Politique des formulaires de création et de réinitialisation.

Les mots de passe existants restent utilisables, notamment après une reprise.
Le wizard Windows applique la même longueur minimale.
"""
MIN_PASSWORD_LENGTH = 12
PASSWORD_REQUIREMENT = "Le mot de passe doit contenir au moins 12 caractères."
