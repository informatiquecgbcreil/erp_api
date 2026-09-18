"""L'installateur Windows : ce qu'il écrit doit pouvoir être relu.

L'installateur s'adresse explicitement à « une personne sans connaissances
en informatique ». Quand il se trompe, cette personne n'a aucun moyen de
comprendre pourquoi — elle voit une exception .NET ou une application qui
refuse de démarrer, et elle s'arrête là.

Ces tests ne lancent pas PowerShell (impossible ici). Ils vérifient deux
choses qu'on peut vérifier sans l'exécuter :

1. **le comportement** de ce qu'il produit — une URL de connexion doit se
   relire correctement, y compris avec un mot de passe biscornu ;
2. **l'ordre des opérations**, quand cet ordre est la correction d'un bug.
"""
import pathlib
import re
from urllib.parse import quote

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
INSTALLATEUR = RACINE / "installation" / "Installer.ps1"


@pytest.fixture(scope="module")
def script() -> str:
    return INSTALLATEUR.read_text(encoding="utf-8")


def _escape_data_string(valeur: str) -> str:
    """Équivalent Python de [uri]::EscapeDataString du script PowerShell."""
    return quote(valeur, safe="")


# ---------------------------------------------------------------------------
# Le mot de passe dans l'URL de connexion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mdp", [
    "Xk7mPq2nRt",      # celui que l'installateur génère : alphanumérique
    "P@ssw0rd!",       # celui qu'un humain tape : le « @ » est le piège
    "Creil@2026#Num",
    "mot:de:passe@ici",
    "100%sur/le@net",
    "avec des espaces",
])
def test_un_mot_de_passe_encode_se_relit_intact(mdp):
    """Le mot de passe part dans une URL : il doit être encodé.

    Sans encodage, un « @ » coupe l'URL au mauvais endroit et l'hôte devient
    un morceau du mot de passe. L'application échoue alors sur une erreur de
    résolution de nom, qui ne parle ni de mot de passe ni d'URL.
    """
    from sqlalchemy.engine import make_url

    url = make_url(
        f"postgresql+psycopg://appgestion:{_escape_data_string(mdp)}"
        f"@127.0.0.1:5432/appgestion"
    )
    assert url.password == mdp
    assert url.host == "127.0.0.1"
    assert url.database == "appgestion"
    assert url.username == "appgestion"


def test_sans_encodage_un_arobase_casse_tout():
    """La démonstration du bug, pour que personne ne « simplifie » l'encodage.

    Ce n'est pas théorique : l'installateur propose explicitement de saisir
    son propre mot de passe, et « @ » est le caractère spécial le plus
    courant dans un mot de passe choisi par un humain.
    """
    from sqlalchemy.engine import make_url

    url = make_url("postgresql+psycopg://appgestion:P@ssw0rd@127.0.0.1:5432/appgestion")
    assert url.host != "127.0.0.1", "si ceci passe, le piège a disparu — tant mieux"
    assert url.password != "P@ssw0rd"


def test_linstallateur_encode_le_mot_de_passe(script):
    assert "[uri]::EscapeDataString($mdpApp)" in script, (
        "le mot de passe doit être encodé avant d'entrer dans la DATABASE_URL"
    )
    ligne = next(l for l in script.splitlines() if l.startswith("DATABASE_URL="))
    assert "$mdpAppUrl" in ligne and "$mdpApp@" not in ligne, ligne


def test_lurl_nomme_son_pilote(script):
    """« postgresql:// » tout nu fait chercher psycopg2 à SQLAlchemy — son
    défaut historique — alors que l'application tourne sur psycopg 3."""
    ligne = next(l for l in script.splitlines() if l.startswith("DATABASE_URL="))
    assert ligne.startswith("DATABASE_URL=postgresql+psycopg://"), ligne


# ---------------------------------------------------------------------------
# L'ordre des opérations
# ---------------------------------------------------------------------------

def _ligne_de(script: str, motif: str) -> int:
    """Le numéro de la première ligne ACTIVE qui correspond.

    Les lignes commentées sont ignorées : sans ça, commenter l'instruction
    suffirait à contourner le test censé la garder — il correspondrait
    encore au texte du commentaire.
    """
    for numero, ligne in enumerate(script.splitlines(), start=1):
        if ligne.strip().startswith("#"):
            continue
        if re.search(motif, ligne):
            return numero
    raise AssertionError(f"introuvable dans l'installateur : {motif}")


def test_le_service_est_arrete_avant_pip(script):
    """Sur une mise à jour, le service tourne encore et son Python garde
    ouverts les modules compilés (.pyd) de psycopg, Pillow…

    pip ne peut alors pas les remplacer — « Access is denied » — et la mise
    à jour échoue au milieu. Or le guide d'installation dit explicitement de
    relancer l'installateur pour mettre à jour : c'est le chemin normal,
    pas un cas tordu.
    """
    arret = _ligne_de(script, r"Stop-Service -Name \"AppGestion\"")
    copie = _ligne_de(script, r"^robocopy \$source")
    pip = _ligne_de(script, r"Installation des dépendances Python")

    assert arret < copie, "le service doit être arrêté avant la copie des fichiers"
    assert arret < pip, "le service doit être arrêté avant que pip ne touche au .venv"


def test_le_telechargement_de_nssm_ne_tue_pas_linstallation(script):
    """nssm.cc est un petit site, parfois injoignable, et certains réseaux
    municipaux le bloquent. Une exception .NET brute devant quelqu'un qui
    n'est pas informaticien, c'est une installation abandonnée."""
    bloc = script[script.index("Téléchargement de NSSM"):]
    bloc = bloc[:bloc.index("Ok \"NSSM téléchargé\"")]

    assert "Tls12" in bloc, (
        "PowerShell 5.1 négocie encore TLS 1.0 sur certaines machines : le "
        "téléchargement échoue alors sur une erreur de canal sécurisé"
    )
    assert "1..3" in bloc, "un seul essai ne suffit pas pour un site parfois lent"
    assert "Contournement" in bloc, (
        "en cas d'échec définitif, le message doit dire quoi faire, pas "
        "seulement que ça a raté"
    )


# ---------------------------------------------------------------------------
# Ce que l'installateur écrit doit exister dans l'application
# ---------------------------------------------------------------------------

def test_toutes_les_variables_ecrites_sont_lues(script):
    """Une variable écrite dans le .env que personne ne lit est un réglage
    qui ne sert à rien — et l'utilisateur croit avoir configuré quelque
    chose. Le cas le plus coûteux : l'accès réseau.
    """
    bloc = script[script.index("ERP_ENV="):script.index("BACKUP_OFFSITE_DIRS=") + 20]
    ecrites = {
        ligne.split("=", 1)[0].strip()
        for ligne in bloc.splitlines()
        if "=" in ligne and not ligne.strip().startswith("#")
    }
    assert "ERP_HOST" in ecrites and "DATABASE_URL" in ecrites, ecrites

    sources = "\n".join(
        chemin.read_text(encoding="utf-8", errors="ignore")
        for chemin in [RACINE / "config.py", RACINE / "run_waitress.py"]
        + list((RACINE / "app").rglob("*.py"))
    )
    jamais_lues = [nom for nom in sorted(ecrites) if f'"{nom}"' not in sources
                   and f"'{nom}'" not in sources]
    assert not jamais_lues, (
        f"variables écrites dans le .env mais lues nulle part : {jamais_lues}"
    )


def test_les_fichiers_appeles_par_linstallateur_existent(script):
    """Un chemin qui a bougé ne se voit qu'au moment de l'installation, chez
    l'utilisateur, et souvent trop tard."""
    for chemin in ("run_waitress.py", "tools/backup_instance.py", "requirements.txt"):
        assert (RACINE / chemin).exists(), chemin
        assert chemin.split("/")[-1] in script, f"{chemin} n'est plus référencé"
