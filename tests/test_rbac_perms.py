"""Gel du comportement RBAC autour des équivalences/alias de permissions.

Ces tests verrouillent l'état AVANT/APRÈS le renommage de la 1ʳᵉ structure
``PERM_EQUIVALENTS`` (qui partageait son nom avec une seconde, plus bas) :
le comportement observable ne doit pas changer.
"""
from app.rbac import HIDDEN_LEGACY_PERMS, _expand_perm


def test_hidden_legacy_perms_inchanges():
    # La liste « à masquer dans l'UI RBAC » : uniquement des alias legacy.
    assert HIDDEN_LEGACY_PERMS == {
        "users:edit", "budget:delete", "projets_edit",
        "participants:update", "participants:write", "participant:edit",
        "bilan:view", "bilans:lourds:view",
    }


def test_hidden_ne_masque_pas_de_permissions_actuelles():
    # Garde-fou anti-régression : des permissions RÉELLES ne doivent jamais
    # se retrouver masquées de l'écran d'administration.
    for perm_actuelle in ("stats:view", "statsimpact:view_all", "projets:edit", "admin:users"):
        assert perm_actuelle not in HIDDEN_LEGACY_PERMS


def test_expand_perm_runtime_inchange():
    assert _expand_perm("projets_edit") == {"projets_edit", "projets:edit", "aap:view"}
    assert _expand_perm("stats:view") == {"stats:view", "stats:view_all"}
    assert _expand_perm("inconnu") == {"inconnu"}
    assert _expand_perm("") == set()
