"""Tests de la journalisation des erreurs (fichier avec rotation)."""
import logging
import os


def _lire_journal() -> str:
    chemin = os.path.join(os.environ["ERP_LOG_DIR"], "erreurs.log")
    assert os.path.exists(chemin), "le fichier de journal doit être créé au démarrage"
    with open(chemin, encoding="utf-8") as fh:
        return fh.read()


def test_les_erreurs_sont_journalisees(app):
    app.logger.error("message d'erreur de test %s", "JOURNAL-ERREUR")
    contenu = _lire_journal()
    assert "JOURNAL-ERREUR" in contenu
    assert "ERROR" in contenu


def test_les_avertissements_sont_journalises(app):
    app.logger.warning("avertissement de test %s", "JOURNAL-AVERTISSEMENT")
    assert "JOURNAL-AVERTISSEMENT" in _lire_journal()


def test_le_niveau_info_nest_pas_journalise(app):
    app.logger.setLevel(logging.DEBUG)
    app.logger.info("détail de test %s", "JOURNAL-INFO")
    assert "JOURNAL-INFO" not in _lire_journal()
