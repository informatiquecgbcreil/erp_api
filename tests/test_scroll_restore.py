"""Test du maintien de la position de défilement après une action.

Le comportement (JavaScript) n'est pas exécutable par le client de test
Flask. Ce test garantit au minimum que le script global est bien injecté
dans le gabarit commun — pour qu'une suppression accidentelle casse la CI.
"""


def test_script_maintien_scroll_present(admin_client):
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "Maintien de la position de défilement" in page
    assert "scrollpos:" in page
    assert "data-no-scroll-restore" in page


def test_script_present_sur_page_longue(admin_client):
    # Page typiquement longue où l'irritant se manifeste (liste subventions)
    page = admin_client.get("/subventions").get_data(as_text=True)
    assert "scrollpos:" in page
