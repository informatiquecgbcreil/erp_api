"""Territoire du centre : la fin du territoire de Creil codé en dur.

Ce que ces tests garantissent :

1. **Creil ne bouge pas.** Avec le territoire historique (celui que la
   migration reprend), le bilan SENACS classe chaque habitant exactement
   comme l'ancien code — recopié ici tel qu'il était — sur un jeu de
   quartiers choisis pour être piégeux.
2. **Un autre centre obtient ses propres lignes** : zéro, un ou plusieurs
   QPV suivis, des quartiers distingués ou non, ses « Habitants de … ».
3. **Une base neuve n'hérite pas de Creil**, et une base de Creil le garde.
4. Les écrans (Administration > Identité, assistant de premier démarrage)
   enregistrent le territoire sans écraser ce qu'ils ne montrent pas.
"""
import uuid
from datetime import date

import pytest

from app.services.territoire import TERRITOIRE_HISTORIQUE, Territoire, lire_liste

CREIL = Territoire(
    ville_siege="Creil",
    code_postal="60100",
    qpv_suivis=("Hauts de Creil",),
    quartiers_distingues=("Rouher",),
    latitude=49.2583,
    longitude=2.4750,
)


def _suffixe() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture
def territoire_regle(app):
    """Règle un autre territoire le temps d'un test, puis rétablit Creil."""
    from app.extensions import db
    from app.models import InstanceSettings
    from app.services.territoire import oublier

    def regler(**valeurs):
        with app.app_context():
            reglages = InstanceSettings.query.first()
            for colonne in TERRITOIRE_HISTORIQUE:
                setattr(reglages, colonne, valeurs.get(colonne))
            db.session.commit()
            oublier()

    yield regler
    with app.app_context():
        reglages = InstanceSettings.query.first()
        for colonne, valeur in TERRITOIRE_HISTORIQUE.items():
            setattr(reglages, colonne, valeur)
        db.session.commit()
        oublier()


# ---------------------------------------------------------------------------
# Lecture des réglages
# ---------------------------------------------------------------------------

def test_une_valeur_par_ligne_sans_doublon_ni_vide():
    assert lire_liste("Saint-Jean\n\n  Argentine \nsaint-jean;Notre-Dame") == (
        "Saint-Jean", "Argentine", "Notre-Dame")
    assert lire_liste(None) == ()
    assert lire_liste("   ") == ()


def test_la_ville_siege_se_reconnait_quelle_que_soit_l_ecriture():
    nogent = Territoire(ville_siege="Nogent-sur-Oise")
    assert nogent.habite_ville_siege("NOGENT SUR OISE")
    assert nogent.habite_ville_siege("nogent-sur-oise (60)")
    assert not nogent.habite_ville_siege("Creil")
    assert not nogent.habite_ville_siege(None)
    # Sans ville siège réglée, personne n'y habite.
    assert not Territoire().habite_ville_siege("Creil")
    assert Territoire().libelle_ville == "Ville siège"


# ---------------------------------------------------------------------------
# 1. Creil : identique à l'ancien calcul
# ---------------------------------------------------------------------------

def _ancien_bucket(p, villes_qpv):
    """Le classement SENACS tel qu'il était codé avant le réglage du territoire."""
    from app.services.recherche_texte import sans_accent

    quartier_distingue, qpv_de_reference = "Rouher", "Hauts de Creil"
    quartier = p.quartier
    if quartier is None:
        ville = (sans_accent(p.ville) or "").strip()
        if not ville:
            return "Non renseigné"
        return "Non renseigné" if ville in villes_qpv else "Hors QPV"
    nom = sans_accent(quartier.nom) or ""
    qpv = (getattr(quartier, "qpv", None) or "").strip()
    if sans_accent(quartier_distingue) in nom:
        return quartier_distingue
    if qpv:
        return qpv_de_reference if sans_accent(qpv) == sans_accent(qpv_de_reference) else "Autre QPV"
    if sans_accent(qpv_de_reference) in nom or "hauts-de-creil" in nom:
        return qpv_de_reference
    if getattr(quartier, "is_qpv", False) or "qpv" in nom:
        return "Autre QPV"
    if ((quartier.ville or "") or (p.ville or "")).strip():
        return "Hors QPV"
    return "Non renseigné"


def _anciennes_villes_avec_qpv():
    from app.models import Quartier
    from app.services.recherche_texte import sans_accent

    villes = set()
    for q in Quartier.query.all():
        nom = sans_accent(q.nom) or ""
        if ((q.qpv or "").strip() or q.is_qpv or "qpv" in nom or "hauts de creil" in nom
                or "rouher" in nom) and (q.ville or "").strip():
            villes.add((sans_accent(q.ville) or "").strip())
    return villes


def _ancien_is_qpv(p):
    if not p.quartier:
        return False
    if (p.quartier.qpv or "").strip() or p.quartier.is_qpv:
        return True
    ville = (p.quartier.ville or "").strip().lower()
    nom = (p.quartier.nom or "").strip().lower()
    return "qpv" in nom or (ville == "creil" and ("rouher" in nom or "hauts de creil" in nom))


#: (ville, nom, qpv, is_qpv) — des cas réels et des cas tordus.
QUARTIERS_PIEGES = [
    ("Creil", "Rouher", "Hauts de Creil", True),
    ("Creil", "Rouher", None, False),
    ("Creil", "Rouher (QPV Hauts de Creil)", None, False),
    ("Creil", "Cavée de Senlis", "Hauts de Creil", True),
    ("Creil", "Cavée de Senlis", None, False),
    ("Creil", "Hauts de Creil", None, False),
    ("Creil", "hauts-de-creil nord", None, False),
    ("Creil", "Bas de Creil", None, False),
    ("Creil", "Plateau", "HAUTS DE CRÉIL", True),
    ("Creil", "Moulin", "Autre quartier prioritaire", True),
    ("Creil", "Gournay", None, True),
    ("Creil", "Quartier QPV sans nom", None, False),
    ("Nogent-sur-Oise", "Les Granges", "Les Granges", True),
    ("Nogent-sur-Oise", "Centre", None, False),
    ("Montataire", "Rouher-bis", None, False),
    ("", "Sans ville", None, False),
]


def test_creil_obtient_exactement_les_memes_chiffres_qu_avant(app):
    from app.extensions import db
    from app.models import Participant, Quartier
    from app.services.senacs import _bucket_quartier, villes_avec_qpv
    from app.services.territoire import territoire

    suf = _suffixe()
    with app.app_context():
        # La base de test joue la base historique : son territoire est Creil.
        assert territoire() == CREIL

        habitants = []
        for i, (ville, nom, qpv, is_qpv) in enumerate(QUARTIERS_PIEGES):
            quartier = Quartier(ville=ville, nom=f"{nom} {suf}{i}", qpv=qpv, is_qpv=is_qpv)
            db.session.add(quartier)
            db.session.flush()
            habitants.append(Participant(nom=f"Piege{suf}{i}", prenom="X", ville=ville or None,
                                         quartier_id=quartier.id))
        for ville in ("Creil", "CREIL", "Nogent-sur-Oise", "Laigneville", ""):
            habitants.append(Participant(nom=f"SansQuartier{suf}", prenom=ville or "vide", ville=ville or None))
        db.session.add_all(habitants)
        db.session.commit()

        villes_qpv = villes_avec_qpv()
        assert villes_qpv == _anciennes_villes_avec_qpv()
        for p in habitants:
            attendu = _ancien_bucket(p, villes_qpv)
            assert _bucket_quartier(p, villes_qpv) == attendu, (p.ville, p.quartier and p.quartier.nom)
            if p.quartier is not None and p.quartier.nom.startswith("hauts-de-creil"):
                # Seule correction voulue : écrit avec des tirets et sans QPV
                # renseigné, ce quartier comptait déjà aux Hauts de Creil dans
                # le bilan SENACS, mais pas dans le taux QPV des indicateurs.
                assert _ancien_is_qpv(p) is False and p.is_qpv is True
                continue
            assert p.is_qpv == _ancien_is_qpv(p), (p.ville, p.quartier and p.quartier.nom)


# ---------------------------------------------------------------------------
# 2. Un autre centre : ses QPV, ses quartiers, sa ville
# ---------------------------------------------------------------------------

def _habitant(app, ville, nom_quartier=None, qpv=None, is_qpv=False):
    from app.extensions import db
    from app.models import Participant, Quartier

    suf = _suffixe()
    quartier_id = None
    if nom_quartier:
        quartier = Quartier(ville=ville, nom=f"{nom_quartier} {suf}", qpv=qpv, is_qpv=is_qpv)
        db.session.add(quartier)
        db.session.flush()
        quartier_id = quartier.id
    p = Participant(nom=f"Hab{suf}", prenom="X", ville=ville, quartier_id=quartier_id)
    db.session.add(p)
    db.session.flush()
    return p


@pytest.mark.parametrize("qpv_suivis, attendus", [
    # Un centre rural ou sans QPV suivi : tous les QPV sur une seule ligne.
    ((), {"Argentine": "QPV", "Saint-Jean": "QPV", "Notre-Dame": "QPV"}),
    # Un QPV suivi : les autres sont « Autre QPV ».
    (("Saint-Jean",), {"Argentine": "Autre QPV", "Saint-Jean": "Saint-Jean", "Notre-Dame": "Autre QPV"}),
    # Trois QPV suivis : chacun sa ligne.
    (("Saint-Jean", "Argentine", "Notre-Dame"),
     {"Argentine": "Argentine", "Saint-Jean": "Saint-Jean", "Notre-Dame": "Notre-Dame"}),
])
def test_zero_un_ou_plusieurs_qpv_suivis(app, qpv_suivis, attendus):
    from app.services.senacs import _bucket_quartier

    beauvais = Territoire(ville_siege="Beauvais", qpv_suivis=qpv_suivis)
    with app.app_context():
        for qpv, attendu in attendus.items():
            p = _habitant(app, "Beauvais", f"Quartier {qpv}", qpv=qpv, is_qpv=True)
            assert _bucket_quartier(p, set(), beauvais) == attendu
        # Hors QPV et quartier inconnu ne dépendent pas des QPV suivis.
        assert _bucket_quartier(_habitant(app, "Beauvais", "Centre-ville"), set(), beauvais) == "Hors QPV"
        assert _bucket_quartier(_habitant(app, ""), set(), beauvais) == "Non renseigné"
        from app.extensions import db
        db.session.rollback()


def test_rien_de_creil_dans_le_bilan_d_un_autre_centre(app):
    """Un quartier nommé « Rouher » ou « Hauts de Creil » n'a plus de
    ligne à lui chez un centre qui ne l'a pas demandé."""
    from app.services.senacs import _bucket_quartier

    beauvais = Territoire(ville_siege="Beauvais", qpv_suivis=("Saint-Jean",))
    with app.app_context():
        assert _bucket_quartier(_habitant(app, "Beauvais", "Rouher", is_qpv=True), set(), beauvais) == "Autre QPV"
        assert _bucket_quartier(_habitant(app, "Beauvais", "Hauts de Creil"), set(), beauvais) == "Hors QPV"
        from app.extensions import db
        db.session.rollback()


def test_un_quartier_distingue_garde_sa_ligne_avant_son_qpv(app):
    from app.services.senacs import _bucket_quartier

    beauvais = Territoire(ville_siege="Beauvais", qpv_suivis=("Saint-Jean",),
                          quartiers_distingues=("Les Champs Dolent",))
    with app.app_context():
        distingue = _habitant(app, "Beauvais", "Les Champs Dolent", qpv="Saint-Jean", is_qpv=True)
        voisin = _habitant(app, "Beauvais", "Rue de Calais", qpv="Saint-Jean", is_qpv=True)
        assert _bucket_quartier(distingue, set(), beauvais) == "Les Champs Dolent"
        assert _bucket_quartier(voisin, set(), beauvais) == "Saint-Jean"
        from app.extensions import db
        db.session.rollback()


ANNEE_BILAN = 1997  # aucune autre donnée de test : le bilan ne compte que les nôtres


def test_bilan_senacs_et_indicateurs_d_un_centre_de_beauvais(app, territoire_regle):
    """De bout en bout : régler le territoire change le bilan et les indicateurs."""
    from app.extensions import db
    from app.models import AtelierActivite, PresenceActivite, SessionActivite
    from app.services.indicators import _add_demography_metrics
    from app.services.senacs import publics_annee

    territoire_regle(territoire_ville="Beauvais", territoire_qpv_suivis="Saint-Jean\nArgentine",
                     territoire_quartiers_distingues=None)
    with app.app_context():
        habitants = [
            _habitant(app, "Beauvais", "Saint-Jean", qpv="Saint-Jean", is_qpv=True),
            _habitant(app, "BEAUVAIS", "Argentine", qpv="Argentine", is_qpv=True),
            _habitant(app, "Beauvais", "Centre-ville"),
            _habitant(app, "Allonne"),
            _habitant(app, "Creil", "Rouher", qpv="Hauts de Creil", is_qpv=True),
        ]
        atelier = AtelierActivite(nom=f"Atelier Beauvais {_suffixe()}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(atelier_id=atelier.id, secteur="Numérique",
                                 date_session=date(ANNEE_BILAN, 3, 10), duree_minutes=60)
        db.session.add(seance)
        db.session.flush()
        db.session.add_all([PresenceActivite(session_id=seance.id, participant_id=p.id) for p in habitants])
        db.session.commit()

        quartiers = publics_annee(ANNEE_BILAN)["quartiers"]
        assert quartiers == {"Argentine": 1, "Autre QPV": 1, "Hors QPV": 2, "Saint-Jean": 1}

        mesures = {}
        _add_demography_metrics(mesures, [p.id for p in habitants], date(ANNEE_BILAN, 12, 31))
        assert mesures["participants_creil"] == 3  # le code reste, il compte Beauvais
        assert mesures["participants_qpv"] == 3


def test_les_habitants_de_creil_restent_comptes_a_creil(app):
    from app.services.indicators import _add_demography_metrics

    with app.app_context():
        creillois = _habitant(app, "Creil")
        voisin = _habitant(app, "Nogent-sur-Oise")
        mesures = {}
        _add_demography_metrics(mesures, [creillois.id, voisin.id])
        assert mesures["participants_creil"] == 1
        from app.extensions import db
        db.session.rollback()


# ---------------------------------------------------------------------------
# 3. Carte et attestations
# ---------------------------------------------------------------------------

def test_la_carte_s_ouvre_sur_la_ville_siege_sinon_sur_la_france(app, territoire_regle):
    from app.services.cartographie import CENTRE_FRANCE, centre_par_defaut

    with app.app_context():
        assert centre_par_defaut() == {"lat": 49.2583, "lon": 2.4750, "zoom": 13}
    territoire_regle(territoire_ville="Beauvais")
    with app.app_context():
        assert centre_par_defaut() == CENTRE_FRANCE
    territoire_regle(territoire_ville="Beauvais", territoire_latitude=49.43, territoire_longitude=2.08)
    with app.app_context():
        assert centre_par_defaut() == {"lat": 49.43, "lon": 2.08, "zoom": 13}


def test_la_page_carte_porte_le_centre_regle(admin_client, territoire_regle):
    territoire_regle(territoire_ville="Beauvais", territoire_latitude=49.43, territoire_longitude=2.08)
    page = admin_client.get("/quartiers/carte").get_data(as_text=True)
    assert 'data-centre-lat="49.43"' in page
    assert "49.2583" not in page


def test_les_attestations_sont_faites_a_la_ville_siege(app, territoire_regle):
    from app.activite.helpers import _attestation_document_fields

    with app.test_request_context("/"):
        assert _attestation_document_fields()["city"] == "Creil"
    territoire_regle(territoire_ville="Beauvais")
    with app.test_request_context("/"):
        assert _attestation_document_fields()["city"] == "Beauvais"
    with app.test_request_context("/?city=Allonne"):
        assert _attestation_document_fields()["city"] == "Allonne"


# ---------------------------------------------------------------------------
# 4. Reprise des bases existantes
# ---------------------------------------------------------------------------

def test_une_base_neuve_n_herite_pas_de_creil(fresh_app):
    from app.services.territoire import territoire

    with fresh_app.app_context():
        # (L'assistant de premier démarrage a pu y régler une ville.)
        terr = territoire()
        assert terr.ville_siege != "Creil"
        assert terr.qpv_suivis == () and terr.quartiers_distingues == ()


def test_la_reprise_pose_creil_sur_une_base_de_creil_sans_rien_ecraser(app, territoire_regle):
    from app.extensions import db
    from app.models import InstanceSettings
    from app.services.territoire import reprendre_territoire_historique

    with app.app_context():
        _habitant(app, "Creil")
        db.session.commit()

    # Déjà réglée (sur Beauvais) : la reprise ne touche à rien.
    territoire_regle(territoire_ville="Beauvais")
    with app.app_context():
        assert reprendre_territoire_historique(db.session.connection()) is False
        db.session.commit()
        assert InstanceSettings.query.first().territoire_ville == "Beauvais"

    # Pas réglée, mais des habitants de Creil : Creil, comme avant.
    territoire_regle()
    with app.app_context():
        assert reprendre_territoire_historique(db.session.connection()) is True
        db.session.commit()
        reglages = InstanceSettings.query.first()
        for colonne, valeur in TERRITOIRE_HISTORIQUE.items():
            assert getattr(reglages, colonne) == valeur
        # Rejouable.
        assert reprendre_territoire_historique(db.session.connection()) is False


# ---------------------------------------------------------------------------
# 5. Les écrans
# ---------------------------------------------------------------------------

def test_administration_enregistre_le_territoire(admin_client, app, territoire_regle, monkeypatch):
    from app.models import InstanceSettings
    from app.services import territoire as service

    appels = []

    def localiser(ville, code_postal):
        appels.append((ville, code_postal))
        return {"lat": 49.43, "lon": 2.08}

    monkeypatch.setattr(service, "localiser_ville_siege", localiser)
    territoire_regle()
    r = admin_client.post("/admin/instance", data={
        "app_name": "", "organization_name": "",
        "territoire_ville": "beauvais", "territoire_code_postal": "60 000",
        "territoire_qpv_suivis": "Saint-Jean\r\nArgentine\r\n",
        "territoire_quartiers_distingues": "",
    })
    assert r.status_code == 302
    assert appels == [("Beauvais", "60000")]
    with app.app_context():
        reglages = InstanceSettings.query.first()
        assert reglages.territoire_ville == "Beauvais"
        assert reglages.territoire_code_postal == "60000"
        assert reglages.territoire_qpv_suivis == "Saint-Jean\nArgentine"
        assert reglages.territoire_quartiers_distingues is None
        assert (reglages.territoire_latitude, reglages.territoire_longitude) == (49.43, 2.08)

    # Réafficher la page : un QPV par ligne dans le champ.
    page = admin_client.get("/admin/instance").get_data(as_text=True)
    assert "Saint-Jean\nArgentine</textarea>" in page

    # Réenregistrer sans changer de ville ne relance pas la recherche.
    admin_client.post("/admin/instance", data={
        "territoire_ville": "Beauvais", "territoire_code_postal": "60000",
        "territoire_qpv_suivis": "Saint-Jean", "territoire_quartiers_distingues": "",
    })
    assert len(appels) == 1


def test_sans_internet_la_ville_est_gardee_et_on_previent(admin_client, app, territoire_regle, monkeypatch):
    from app.models import InstanceSettings
    from app.services import territoire as service

    monkeypatch.setattr(service, "localiser_ville_siege", lambda ville, cp: None)
    territoire_regle()
    r = admin_client.post("/admin/instance", data={"territoire_ville": "Beauvais"}, follow_redirects=True)
    assert "pas pu être placée sur la carte" in r.get_data(as_text=True)
    with app.app_context():
        reglages = InstanceSettings.query.first()
        assert reglages.territoire_ville == "Beauvais"
        assert reglages.territoire_latitude is None


def test_un_formulaire_sans_les_champs_territoire_n_efface_rien(admin_client, app):
    """Un autre formulaire de la même table (ou un vieil onglet ouvert)
    ne doit pas remettre le territoire à zéro."""
    from app.models import InstanceSettings

    admin_client.post("/admin/instance", data={"app_name": "", "organization_name": ""})
    with app.app_context():
        reglages = InstanceSettings.query.first()
        for colonne, valeur in TERRITOIRE_HISTORIQUE.items():
            assert getattr(reglages, colonne) == valeur
