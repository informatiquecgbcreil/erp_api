"""Lot 2 : plannings, contraintes de temps, salle de référence.

L'enjeu de ce lot n'est pas le calcul — il est dans le lot 1 — mais la
RESTITUTION : ce que l'équipe voit au mur, et le fait que programmer un
atelier remplisse le planning sans que personne y pense.
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.extensions import db
from app.models import AtelierActivite, Espace, Occupation, SessionActivite, Site
from app.services.salles import (
    planning_du_jour,
    planning_mois,
    planning_semaine,
    salle_par_defaut,
)
from app.services.temps_ouverture import (
    HORAIRES_DEFAUT,
    ecrire_horaires,
    feries_entre,
    hors_ouverture,
    jour_ferie,
    jours_feries,
    lire_horaires,
    semaine_de,
)

# Un mercredi, pour que la semaine testée déborde des deux côtés.
MERCREDI = date(2026, 10, 14)


@pytest.fixture()
def batiment(app):
    with app.app_context():
        site = Site(nom="Centre (planning)", code=f"test-pl-{uuid4().hex[:8]}")
        db.session.add(site)
        db.session.flush()
        grande = Espace(site=site, nom="Grande salle", louable=True, capacite_usage=80)
        demi_a = Espace(site=site, parent=grande, nom="Partie A", louable=True, capacite_usage=40)
        atelier = Espace(site=site, nom="Atelier numérique", capacite_usage=12)
        db.session.add_all([grande, demi_a, atelier])
        db.session.commit()
        ids = {"site": site.id, "grande": grande.id, "demi_a": demi_a.id, "atelier": atelier.id}
        site_id = site.id

    # Le contexte applicatif est REFERMÉ avant le yield : le garder ouvert
    # pendant le test le ferait réutiliser par les requêtes du client HTTP,
    # qui liraient alors des objets restés en cache dans la session au lieu
    # de l'état réel en base. C'est exactement ce qui se passe en production,
    # où chaque requête a son propre contexte.
    yield ids

    with app.app_context():
        reste = db.session.get(Site, site_id)
        if reste is not None:
            db.session.delete(reste)
            db.session.commit()


def _occuper(espace_id, jour=MERCREDI, debut=840, fin=960, origine="location", titre="Asso de gym"):
    occ = Occupation(
        espace_id=espace_id, date_jour=jour, minute_debut=debut, minute_fin=fin,
        origine=origine, statut="confirme", titre=titre,
    )
    db.session.add(occ)
    db.session.commit()
    return occ


# ---------------------------------------------------------------------------
# Jours fériés
# ---------------------------------------------------------------------------

def test_onze_feries_par_an():
    for annee in (2024, 2025, 2026, 2027, 2030):
        assert len(jours_feries(annee)) == 11


def test_feries_mobiles_suivent_paques():
    """Les dates de Pâques 2026 et 2027 sont vérifiables dans n'importe quel
    almanach : c'est le meilleur garde-fou contre une erreur d'algorithme."""
    f2026 = jours_feries(2026)
    assert f2026[date(2026, 4, 6)] == "Lundi de Pâques"
    assert f2026[date(2026, 5, 14)] == "Ascension"
    assert f2026[date(2026, 5, 25)] == "Lundi de Pentecôte"
    f2027 = jours_feries(2027)
    assert f2027[date(2027, 3, 29)] == "Lundi de Pâques"


def test_feries_fixes():
    assert jour_ferie(date(2026, 1, 1)) == "Jour de l'an"
    assert jour_ferie(date(2026, 5, 1)) == "Fête du Travail"
    assert jour_ferie(date(2026, 12, 25)) == "Noël"
    assert jour_ferie(date(2026, 12, 26)) is None  # pas l'Alsace-Moselle


def test_feries_a_cheval_sur_deux_annees():
    trouves = feries_entre(date(2025, 12, 20), date(2026, 1, 5))
    assert set(trouves) == {date(2025, 12, 25), date(2026, 1, 1)}


# ---------------------------------------------------------------------------
# Horaires d'ouverture
# ---------------------------------------------------------------------------

def test_site_sans_horaires_ne_contraint_rien(app, batiment):
    """Une équipe qui n'a pas rempli ce réglage ne doit pas être bloquée."""
    with app.app_context():
        site = db.session.get(Site, batiment["site"])
        assert hors_ouverture(site, MERCREDI, 540, 720) is None


def test_hors_horaires_prevenu_mais_pas_interdit(app, batiment):
    with app.app_context():
        site = db.session.get(Site, batiment["site"])
        site.horaires_json = ecrire_horaires(HORAIRES_DEFAUT)
        db.session.commit()

        assert hors_ouverture(site, MERCREDI, 600, 720) is None          # 10h-12h : ouvert
        assert hors_ouverture(site, MERCREDI, 1200, 1320) is not None    # 20h-22h : après fermeture
        dimanche = MERCREDI + timedelta(days=4)
        assert dimanche.weekday() == 6
        assert "fermé" in hors_ouverture(site, dimanche, 600, 720)


def test_un_ferie_ferme_le_centre(app, batiment):
    with app.app_context():
        site = db.session.get(Site, batiment["site"])
        site.horaires_json = ecrire_horaires(HORAIRES_DEFAUT)
        db.session.commit()
        message = hors_ouverture(site, date(2026, 5, 1), 600, 720)
        assert message and "Fête du Travail" in message


def test_horaires_illisibles_ne_font_pas_tomber_la_page(app, batiment):
    with app.app_context():
        site = db.session.get(Site, batiment["site"])
        site.horaires_json = "{ ceci n'est pas du JSON"
        db.session.commit()
        assert lire_horaires(site) == {}
        assert hors_ouverture(site, MERCREDI, 600, 720) is None


# ---------------------------------------------------------------------------
# Plannings
# ---------------------------------------------------------------------------

def test_semaine_du_lundi_au_dimanche():
    jours = semaine_de(MERCREDI)
    assert len(jours) == 7
    assert jours[0].weekday() == 0 and jours[-1].weekday() == 6
    assert MERCREDI in jours


def test_grille_semaine_repercute_sur_la_salle_parente(app, batiment):
    """Réserver une moitié doit SE VOIR sur la ligne de la grande salle,
    sinon la secrétaire la proposera quand même."""
    with app.app_context():
        _occuper(batiment["demi_a"])
        grille = planning_semaine(batiment["site"], MERCREDI)

        par_salle = {l["espace"].nom: l for l in grille["lignes"]}
        assert set(par_salle) == {"Grande salle", "Partie A", "Atelier numérique"}

        mercredi = [c for c in par_salle["Grande salle"]["cellules"] if c["jour"] == MERCREDI][0]
        assert len(mercredi["evenements"]) == 1
        # Signalée comme indirecte : c'est Partie A qui est prise, pas la grande.
        assert mercredi["evenements"][0]["direct"] is False

        directe = [c for c in par_salle["Partie A"]["cellules"] if c["jour"] == MERCREDI][0]
        assert directe["evenements"][0]["direct"] is True

        # Une salle sans lien ne reçoit rien.
        libre = [c for c in par_salle["Atelier numérique"]["cellules"] if c["jour"] == MERCREDI][0]
        assert libre["evenements"] == []


def test_grille_semaine_marque_les_feries(app, batiment):
    with app.app_context():
        grille = planning_semaine(batiment["site"], date(2026, 5, 1))
        assert date(2026, 5, 1) in grille["feries"]


def test_occupation_annulee_absente_du_planning(app, batiment):
    with app.app_context():
        occ = _occuper(batiment["atelier"])
        occ.statut = "annule"
        db.session.commit()
        grille = planning_semaine(batiment["site"], MERCREDI)
        assert grille["total"] == 0


def test_vue_mois_rectangulaire(app, batiment):
    with app.app_context():
        _occuper(batiment["atelier"])
        mois = planning_mois(2026, 10, site_id=batiment["site"])
        assert all(len(s) == 7 for s in mois["semaines"]), "grille non rectangulaire à l'impression"
        cases = [c for s in mois["semaines"] for c in s]
        assert any(c["evenements"] for c in cases)
        # Les jours des mois voisins sont présents mais marqués.
        assert any(c["hors_mois"] for c in cases)


def test_vue_mois_filtree_sur_une_salle(app, batiment):
    with app.app_context():
        _occuper(batiment["demi_a"], titre="Gym")
        _occuper(batiment["atelier"], titre="Tablettes")

        vue = planning_mois(2026, 10, espace_id=batiment["grande"])
        titres = {e["occupation"].titre for s in vue["semaines"] for c in s for e in c["evenements"]}
        assert titres == {"Gym"}, "la grande salle ne doit voir que ce qui la concerne"


def test_ecran_du_hall_ne_montre_que_le_direct(app, batiment):
    """Dans un hall, répéter qu'une salle est prise parce que sa moitié l'est
    ne fait qu'embrouiller."""
    with app.app_context():
        _occuper(batiment["demi_a"], titre="Gym")
        lignes = planning_du_jour(batiment["site"], MERCREDI)
        assert [l["occupation"].espace.nom for l in lignes] == ["Partie A"]


def test_ecran_du_hall_trie_par_heure(app, batiment):
    with app.app_context():
        _occuper(batiment["atelier"], debut=600, fin=720, titre="Matin")
        _occuper(batiment["demi_a"], debut=540, fin=600, titre="Tôt")
        lignes = planning_du_jour(batiment["site"], MERCREDI)
        assert [l["occupation"].titre for l in lignes] == ["Tôt", "Matin"]


# ---------------------------------------------------------------------------
# Salle de référence : le cœur de la levée de charge mentale
# ---------------------------------------------------------------------------

def test_salle_de_reference_ignoree_si_inutilisable(app, batiment):
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Test réf", espace_id=batiment["atelier"])
        db.session.add(atelier)
        db.session.commit()
        assert salle_par_defaut(atelier) == batiment["atelier"]

        # Une salle désactivée ne doit plus être héritée en silence.
        espace = db.session.get(Espace, batiment["atelier"])
        espace.actif = False
        db.session.commit()
        assert salle_par_defaut(atelier) is None

        espace.actif = True
        db.session.commit()
        db.session.delete(atelier)
        db.session.commit()


def test_une_seance_creee_herite_de_la_salle_de_latelier(admin_client, app, batiment):
    """Le parcours réel : on programme un atelier, le planning se remplit
    sans que personne n'ouvre l'écran des salles."""
    with app.app_context():
        atelier = AtelierActivite(
            secteur="Numérique", nom="Atelier tablettes", espace_id=batiment["atelier"],
        )
        db.session.add(atelier)
        db.session.commit()
        atelier_id = atelier.id

    reponse = admin_client.post(
        f"/activite/atelier/{atelier_id}/session/new",
        data={
            "date_session": MERCREDI.isoformat(),
            "heure_debut": "14:00", "heure_fin": "16:00",
            "espace_id": str(batiment["atelier"]),
        },
        follow_redirects=True,
    )
    assert reponse.status_code == 200

    with app.app_context():
        seance = SessionActivite.query.filter_by(atelier_id=atelier_id).one()
        assert seance.espace_id == batiment["atelier"]
        occ = Occupation.query.filter_by(session_id=seance.id).one()
        assert occ.origine == "seance" and occ.minute_debut == 840

        grille = planning_semaine(batiment["site"], MERCREDI)
        par_salle = {l["espace"].nom: l for l in grille["lignes"]}
        case = [c for c in par_salle["Atelier numérique"]["cellules"] if c["jour"] == MERCREDI][0]
        assert case["evenements"], "la séance doit apparaître au planning"

        db.session.delete(seance)
        db.session.delete(db.session.get(AtelierActivite, atelier_id))
        db.session.commit()


def test_hors_les_murs_respecte(admin_client, app, batiment):
    """Un choix explicite « aucune salle » ne doit pas être écrasé par la
    salle de référence de l'atelier."""
    with app.app_context():
        atelier = AtelierActivite(
            secteur="Numérique", nom="Atelier sortie", espace_id=batiment["atelier"],
        )
        db.session.add(atelier)
        db.session.commit()
        atelier_id = atelier.id

    admin_client.post(
        f"/activite/atelier/{atelier_id}/session/new",
        data={
            "date_session": MERCREDI.isoformat(),
            "heure_debut": "14:00", "heure_fin": "16:00",
            "espace_id": "",
        },
        follow_redirects=True,
    )

    with app.app_context():
        seance = SessionActivite.query.filter_by(atelier_id=atelier_id).one()
        assert seance.espace_id is None
        assert Occupation.query.filter_by(session_id=seance.id).count() == 0
        db.session.delete(seance)
        db.session.delete(db.session.get(AtelierActivite, atelier_id))
        db.session.commit()


def test_salle_deja_prise_previent_sans_bloquer(admin_client, app, batiment):
    """Refuser l'enregistrement ferait surtout perdre la séance."""
    with app.app_context():
        _occuper(batiment["atelier"], debut=840, fin=960, titre="Déjà là")
        atelier = AtelierActivite(
            secteur="Numérique", nom="Atelier en conflit", espace_id=batiment["atelier"],
        )
        db.session.add(atelier)
        db.session.commit()
        atelier_id = atelier.id

    reponse = admin_client.post(
        f"/activite/atelier/{atelier_id}/session/new",
        data={
            "date_session": MERCREDI.isoformat(),
            "heure_debut": "15:00", "heure_fin": "17:00",
            "espace_id": str(batiment["atelier"]),
        },
        follow_redirects=True,
    )
    page = reponse.get_data(as_text=True)
    assert "déjà occupée" in page or "Déjà là" in page, "le conflit doit être signalé"

    with app.app_context():
        seance = SessionActivite.query.filter_by(atelier_id=atelier_id).one()
        assert seance.espace_id == batiment["atelier"], "la séance doit être enregistrée malgré tout"
        db.session.delete(seance)
        db.session.delete(db.session.get(AtelierActivite, atelier_id))
        db.session.commit()


# ---------------------------------------------------------------------------
# Indisponibilités et écrans
# ---------------------------------------------------------------------------

def test_blocage_sur_plusieurs_jours(admin_client, app, batiment):
    fin = MERCREDI + timedelta(days=4)
    reponse = admin_client.post(
        "/salles/occuper",
        data={
            "espace_id": str(batiment["grande"]), "titre": "Travaux peinture",
            "origine": "blocage",
            "date_debut": MERCREDI.isoformat(), "date_fin": fin.isoformat(),
            "journee_entiere": "1",
        },
        follow_redirects=True,
    )
    assert reponse.status_code == 200

    with app.app_context():
        blocages = Occupation.query.filter_by(
            espace_id=batiment["grande"], origine="blocage"
        ).all()
        assert len(blocages) == 5, "un par jour, bornes comprises"
        # Et ça barre aussi la moitié de salle, par la règle du lot 1.
        from app.services.salles import est_disponible
        assert not est_disponible(
            db.session.get(Espace, batiment["demi_a"]), MERCREDI, "10:00", "11:00"
        )
        for b in blocages:
            db.session.delete(b)
        db.session.commit()


def test_blocage_refuse_une_periode_absurde(admin_client, batiment):
    reponse = admin_client.post(
        "/salles/occuper",
        data={
            "espace_id": str(batiment["grande"]), "titre": "Erreur",
            "origine": "blocage",
            "date_debut": "2026-01-01", "date_fin": "2030-01-01", "journee_entiere": "1",
        },
        follow_redirects=True,
    )
    assert "erreur de saisie" in reponse.get_data(as_text=True)


def test_occupation_de_seance_non_supprimable_a_la_main(admin_client, app, batiment):
    """Elle reviendrait à la prochaine synchronisation : mieux vaut le dire."""
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier protégé",
                                  espace_id=batiment["atelier"])
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique", espace_id=batiment["atelier"],
            date_session=MERCREDI, heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()
        occ_id = seance.occupations[0].id
        ids = (seance.id, atelier.id)

    reponse = admin_client.post(
        f"/salles/occupation/{occ_id}/supprimer", follow_redirects=True
    )
    assert "modifie-la à sa source" in reponse.get_data(as_text=True)

    with app.app_context():
        assert db.session.get(Occupation, occ_id) is not None
        db.session.delete(db.session.get(SessionActivite, ids[0]))
        db.session.delete(db.session.get(AtelierActivite, ids[1]))
        db.session.commit()


def test_les_ecrans_du_lot2_repondent(admin_client, batiment):
    urls = [
        "/salles/planning",
        f"/salles/planning?site_id={batiment['site']}&semaine={MERCREDI.isoformat()}",
        "/salles/planning?impression=1",
        "/salles/planning/mois",
        f"/salles/planning/mois?espace_id={batiment['grande']}",
        "/salles/aujourdhui",
        "/salles/aujourdhui?ecran=1",
        "/salles/occuper",
        f"/salles/site/{batiment['site']}/horaires",
    ]
    for url in urls:
        assert admin_client.get(url).status_code == 200, url


def test_date_illisible_ne_casse_pas_le_planning(admin_client, batiment):
    assert admin_client.get("/salles/planning?semaine=pas-une-date").status_code == 200
    assert admin_client.get("/salles/planning/mois?mois=32-13-9999").status_code == 200


def test_filtre_louables_allege_la_grille_murale(app, admin_client, batiment):
    """Une trentaine de lignes sur un A3, personne ne s'y retrouve : le
    dortoir et le hall n'ont rien à faire sur le planning de location."""
    with app.app_context():
        toutes = planning_semaine(batiment["site"], MERCREDI)
        louables = planning_semaine(batiment["site"], MERCREDI, louables_seulement=True)

        noms_toutes = {l["espace"].nom for l in toutes["lignes"]}
        noms_louables = {l["espace"].nom for l in louables["lignes"]}

        assert "Atelier numérique" in noms_toutes
        assert "Atelier numérique" not in noms_louables, "non louable : hors de la grille filtrée"
        assert {"Grande salle", "Partie A"} <= noms_louables

    page = admin_client.get(
        f"/salles/planning?site_id={batiment['site']}&louables=1"
    ).get_data(as_text=True)
    assert "Atelier numérique" not in page
    assert "Grande salle" in page


# ---------------------------------------------------------------------------
# Lot 2.5 : occuper une salle depuis le planning
# ---------------------------------------------------------------------------

def test_occuper_a_la_main_avec_sa_nature(admin_client, app, batiment):
    """Le geste de base : « telle salle, tel jour, tel créneau, occupée par ça »."""
    reponse = admin_client.post(
        "/salles/occuper",
        data={
            "espace_id": str(batiment["demi_a"]), "titre": "Atelier couture",
            "origine": "interne", "date_debut": MERCREDI.isoformat(),
            "heure_debut": "14:00", "heure_fin": "16:00",
        },
        follow_redirects=True,
    )
    assert reponse.status_code == 200

    with app.app_context():
        occ = Occupation.query.filter_by(titre="Atelier couture").one()
        assert occ.origine == "interne"
        assert (occ.minute_debut, occ.minute_fin) == (840, 960)
        assert occ.pilotee is False, "une saisie manuelle doit rester modifiable"

        # Et elle bloque bien la grande salle, par la règle du lot 1.
        from app.services.salles import est_disponible
        assert not est_disponible(
            db.session.get(Espace, batiment["grande"]), MERCREDI, "15:00", "17:00"
        )
        db.session.delete(occ)
        db.session.commit()


def test_nature_inconnue_retombe_sur_activite(admin_client, app, batiment):
    """Un formulaire bricolé ne doit pas injecter une origine pilotée :
    elle se ferait effacer à la première réconciliation."""
    admin_client.post(
        "/salles/occuper",
        data={
            "espace_id": str(batiment["atelier"]), "titre": "Bricolé",
            "origine": "seance", "date_debut": MERCREDI.isoformat(),
            "heure_debut": "09:00", "heure_fin": "10:00",
        },
        follow_redirects=True,
    )
    with app.app_context():
        occ = Occupation.query.filter_by(titre="Bricolé").one()
        assert occ.origine == "interne"
        db.session.delete(occ)
        db.session.commit()


def test_reconciliation_epargne_les_saisies_manuelles(app, batiment):
    """Le piège : la réconciliation nettoie les occupations pilotées
    orphelines. Une saisie manuelle n'a évidemment ni séance ni créneau et
    ne doit surtout pas être emportée."""
    from app.services.salles import reconcilier_occupations

    with app.app_context():
        for origine in ("interne", "reunion", "location", "blocage"):
            db.session.add(Occupation(
                espace_id=batiment["atelier"], date_jour=MERCREDI,
                minute_debut=540, minute_fin=600, origine=origine,
                statut="confirme", titre=f"Manuel {origine}",
            ))
        db.session.commit()
        avant = Occupation.query.count()

        reconcilier_occupations()
        reconcilier_occupations()

        assert Occupation.query.count() == avant, "une saisie manuelle a été effacée"
        for occ in Occupation.query.filter(Occupation.titre.like("Manuel %")).all():
            db.session.delete(occ)
        db.session.commit()


def test_conflit_signale_mais_occupation_posee(admin_client, app, batiment):
    with app.app_context():
        _occuper(batiment["atelier"], debut=840, fin=960, titre="Déjà pris")

    reponse = admin_client.post(
        "/salles/occuper",
        data={
            "espace_id": str(batiment["atelier"]), "titre": "Par-dessus",
            "origine": "reunion", "date_debut": MERCREDI.isoformat(),
            "heure_debut": "15:00", "heure_fin": "17:00",
        },
        follow_redirects=True,
    )
    page = reponse.get_data(as_text=True)
    assert "Chevauchement" in page

    with app.app_context():
        assert Occupation.query.filter_by(titre="Par-dessus").count() == 1
        for t in ("Déjà pris", "Par-dessus"):
            for occ in Occupation.query.filter_by(titre=t).all():
                db.session.delete(occ)
        db.session.commit()


def test_le_planning_propose_le_clic_par_case(admin_client, batiment):
    """Le geste naturel : voir une case vide, cliquer, remplir. Le lien doit
    emporter la salle ET le jour, sinon on retape ce qu'on avait sous les yeux."""
    page = admin_client.get(
        f"/salles/planning?site_id={batiment['site']}&semaine={MERCREDI.isoformat()}"
    ).get_data(as_text=True)
    assert f"espace_id={batiment['demi_a']}" in page
    assert f"jour={MERCREDI.isoformat()}" in page


def test_formulaire_preremplit_depuis_le_planning(admin_client, batiment):
    page = admin_client.get(
        f"/salles/occuper?espace_id={batiment['grande']}&jour={MERCREDI.isoformat()}"
    ).get_data(as_text=True)
    assert f'value="{MERCREDI.isoformat()}"' in page
    assert f'value="{batiment["grande"]}" selected' in page


def test_ancienne_url_de_blocage_redirige(admin_client):
    """Les liens déjà partagés doivent continuer de marcher, mais l'ancien
    nom ne doit plus apparaître nulle part dans l'application."""
    reponse = admin_client.get("/salles/blocage")
    assert reponse.status_code == 302
    assert "/salles/occuper" in reponse.headers["Location"]
    assert admin_client.get("/salles/blocage", follow_redirects=True).status_code == 200


def test_un_creneau_agenda_peut_occuper_une_salle(admin_client, app, batiment):
    """Mon oubli du lot 2 : le moteur savait le faire, le formulaire ne le
    proposait pas. Une réunion d'équipe doit protéger sa salle."""
    from app.models import AgendaCreneau

    # Le panneau de saisie ne s'ouvre qu'avec un jour ciblé.
    page = admin_client.get(
        f"/mon-agenda/calendrier?jour={MERCREDI.isoformat()}"
    ).get_data(as_text=True)
    assert 'name="espace_id"' in page, "le formulaire doit proposer une salle"

    reponse = admin_client.post(
        "/mon-agenda/creneau",
        data={
            "titre": "Réunion d'équipe", "type_creneau": "reunion",
            "date_creneau": MERCREDI.isoformat(),
            "heure_debut": "10:00", "heure_fin": "12:00",
            "espace_id": str(batiment["demi_a"]),
        },
        follow_redirects=True,
    )
    assert reponse.status_code == 200

    with app.app_context():
        creneau = AgendaCreneau.query.filter_by(titre="Réunion d'équipe").one()
        assert creneau.espace_id == batiment["demi_a"]
        occ = Occupation.query.filter_by(creneau_id=creneau.id).one()
        assert occ.origine == "creneau"

        # La grande salle est protégée : l'accueil ne peut plus louer dessus.
        from app.services.salles import est_disponible
        assert not est_disponible(
            db.session.get(Espace, batiment["grande"]), MERCREDI, "10:30", "11:30"
        )
        db.session.delete(creneau)
        db.session.commit()


def test_la_fiche_de_salle_propose_doccuper(admin_client, batiment):
    page = admin_client.get(f"/salles/espace/{batiment['grande']}").get_data(as_text=True)
    assert f"/salles/occuper?espace_id={batiment['grande']}" in page
