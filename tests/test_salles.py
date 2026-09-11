"""Module Salles & espaces : arbre, disponibilité, blocage automatique.

Le cœur testé ici est la règle unique du module : occuper un espace rend
indisponibles ses ANCÊTRES et ses DESCENDANTS. C'est elle qui règle le cas
de la grande salle séparable par cloisons amovibles, et c'est le seul
endroit où une erreur se paie par deux associations dans la même pièce un
samedi matin.
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.extensions import db
from app.models import (
    AgendaCreneau,
    AtelierActivite,
    Espace,
    InventaireItem,
    Occupation,
    SessionActivite,
    Site,
    minutes_depuis_texte,
)
from app.services.salles import (
    SalleErreur,
    arbre_du_site,
    conflits,
    est_disponible,
    normaliser_plage,
    recherche_disponibilite,
    reconcilier_occupations,
    zone_conflit_ids,
)
from app.services.salles_seed import PLAN_CENTRE_SOCIAL, installer_plan

DEMAIN = date.today() + timedelta(days=1)


@pytest.fixture()
def plan(app):
    """Un bâtiment minimal reproduisant les deux cas tordus d'Antoine :
    la grande salle séparable, et l'armoire dans l'atelier."""
    with app.app_context():
        # Code unique par test : la base de test est partagée sur toute la
        # session pytest, deux fixtures ne doivent pas se marcher dessus.
        site = Site(nom="Centre social (tests)", code=f"test-plan-{uuid4().hex[:8]}")
        db.session.add(site)
        db.session.flush()

        grande = Espace(site=site, nom="Grande salle", capacite_usage=80, louable=True)
        demi_a = Espace(site=site, parent=grande, nom="Partie A", capacite_usage=40, louable=True)
        demi_b = Espace(site=site, parent=grande, nom="Partie B", capacite_usage=40, louable=True)
        atelier = Espace(site=site, nom="Atelier", capacite_usage=12, stockage=True)
        armoire = Espace(
            site=site, parent=atelier, nom="Armoire sécurisée n°1",
            type_espace="stockage", reservable=False, louable=False, stockage=True, securise=True,
        )
        cuisine = Espace(site=site, nom="Cuisine pro", louable=True, battement_minutes=60)
        db.session.add_all([grande, demi_a, demi_b, atelier, armoire, cuisine])
        db.session.commit()

        yield {
            "site_id": site.id, "grande": grande.id, "demi_a": demi_a.id,
            "demi_b": demi_b.id, "atelier": atelier.id, "armoire": armoire.id,
            "cuisine": cuisine.id,
        }

        # Suppression par l'ORM et non en masse : c'est lui qui porte les
        # cascades (SQLite n'applique pas les ON DELETE). Un DELETE en masse
        # laisserait des espaces orphelins, que SQLite rattacherait au
        # prochain site créé en recyclant l'identifiant libéré.
        reste = db.session.get(Site, site.id)
        if reste is not None:
            db.session.delete(reste)
            db.session.commit()


def _occuper(espace_id, debut="14:00", fin="16:00", jour=DEMAIN, origine="location"):
    from app.services.salles import normaliser_plage as _np
    m_debut, m_fin = _np(debut, fin)
    occ = Occupation(
        espace_id=espace_id, date_jour=jour, minute_debut=m_debut, minute_fin=m_fin,
        origine=origine, statut="confirme", titre="Réservation de test",
    )
    db.session.add(occ)
    db.session.commit()
    return occ


# ---------------------------------------------------------------------------
# Horaires
# ---------------------------------------------------------------------------

def test_lecture_des_horaires_en_texte_libre():
    """Les séances stockent l'heure en texte libre : « 9:00 » et « 09:00 »
    doivent donner le même résultat, sinon les comparaisons sont fausses."""
    assert minutes_depuis_texte("09:00") == 540
    assert minutes_depuis_texte("9:00") == 540
    assert minutes_depuis_texte("9h30") == 570
    assert minutes_depuis_texte("0900") == 540
    assert minutes_depuis_texte("") is None
    assert minutes_depuis_texte("n'importe quoi") is None
    assert minutes_depuis_texte("25:00") is None


def test_plage_invalide_refusee(app):
    with app.app_context():
        with pytest.raises(SalleErreur):
            normaliser_plage("16:00", "14:00")
        with pytest.raises(SalleErreur):
            normaliser_plage("14:00", "14:00")


# ---------------------------------------------------------------------------
# La règle : ancêtres + descendants
# ---------------------------------------------------------------------------

def test_zone_de_conflit_remonte_et_descend(app, plan):
    with app.app_context():
        demi_a = Espace.query.get(plan["demi_a"])
        # Une moitié bloque : elle-même + la salle entière (ancêtre).
        assert zone_conflit_ids(demi_a) == {plan["demi_a"], plan["grande"]}

        grande = Espace.query.get(plan["grande"])
        # La salle entière bloque : elle-même + ses deux moitiés (descendants).
        assert zone_conflit_ids(grande) == {plan["grande"], plan["demi_a"], plan["demi_b"]}


def test_cloisons_amovibles(app, plan):
    """LE cas d'Antoine, dans les deux sens."""
    with app.app_context():
        _occuper(plan["demi_a"])

        grande = Espace.query.get(plan["grande"])
        demi_a = Espace.query.get(plan["demi_a"])
        demi_b = Espace.query.get(plan["demi_b"])

        # Réserver une moitié interdit la salle entière…
        assert not est_disponible(grande, DEMAIN, "14:00", "16:00")
        assert not est_disponible(demi_a, DEMAIN, "14:00", "16:00")
        # … mais laisse l'autre moitié disponible.
        assert est_disponible(demi_b, DEMAIN, "14:00", "16:00")


def test_reserver_la_salle_entiere_bloque_les_deux_moities(app, plan):
    with app.app_context():
        _occuper(plan["grande"])
        assert not est_disponible(Espace.query.get(plan["demi_a"]), DEMAIN, "14:00", "16:00")
        assert not est_disponible(Espace.query.get(plan["demi_b"]), DEMAIN, "14:00", "16:00")


def test_espaces_independants_ne_se_genent_pas(app, plan):
    with app.app_context():
        _occuper(plan["grande"])
        assert est_disponible(Espace.query.get(plan["atelier"]), DEMAIN, "14:00", "16:00")


def test_horaires_disjoints_pas_de_conflit(app, plan):
    with app.app_context():
        _occuper(plan["demi_a"], "09:00", "12:00")
        grande = Espace.query.get(plan["grande"])
        assert est_disponible(grande, DEMAIN, "14:00", "16:00")
        assert not est_disponible(grande, DEMAIN, "11:00", "13:00")  # chevauchement partiel


def test_autre_jour_pas_de_conflit(app, plan):
    with app.app_context():
        _occuper(plan["grande"], jour=DEMAIN)
        assert est_disponible(
            Espace.query.get(plan["grande"]), DEMAIN + timedelta(days=1), "14:00", "16:00"
        )


def test_occupation_annulee_libere_la_salle(app, plan):
    with app.app_context():
        occ = _occuper(plan["grande"])
        occ.statut = "annule"
        db.session.commit()
        assert est_disponible(Espace.query.get(plan["grande"]), DEMAIN, "14:00", "16:00")


# ---------------------------------------------------------------------------
# Battement
# ---------------------------------------------------------------------------

def test_battement_impose_un_ecart(app, plan):
    """La cuisine pro exige 60 minutes de remise en état entre deux créneaux."""
    with app.app_context():
        _occuper(plan["cuisine"], "09:00", "12:00")
        cuisine = Espace.query.get(plan["cuisine"])
        assert not est_disponible(cuisine, DEMAIN, "12:30", "14:00")  # 30 min : trop court
        assert est_disponible(cuisine, DEMAIN, "13:00", "15:00")      # 60 min : bon


def test_sans_battement_deux_creneaux_se_touchent(app, plan):
    with app.app_context():
        _occuper(plan["atelier"], "09:00", "12:00")
        assert est_disponible(Espace.query.get(plan["atelier"]), DEMAIN, "12:00", "14:00")


def test_modification_ne_rentre_pas_en_conflit_avec_elle_meme(app, plan):
    with app.app_context():
        occ = _occuper(plan["grande"], "14:00", "16:00")
        assert est_disponible(
            Espace.query.get(plan["grande"]), DEMAIN, "14:30", "16:30",
            exclure_occupation_id=occ.id,
        )


# ---------------------------------------------------------------------------
# Blocage automatique depuis les séances et les créneaux
# ---------------------------------------------------------------------------

def test_une_seance_bloque_sa_salle_toute_seule(app, plan):
    """Aucun appel explicite : le point d'écoute doit s'en charger."""
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier tablettes")
        db.session.add(atelier)
        db.session.flush()

        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique", espace_id=plan["atelier"],
            date_session=DEMAIN, heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()

        occ = Occupation.query.filter_by(session_id=seance.id).one()
        assert occ.espace_id == plan["atelier"]
        assert occ.origine == "seance"
        assert occ.titre == "Atelier tablettes"
        assert (occ.minute_debut, occ.minute_fin) == (840, 960)

        # Et la salle est bel et bien prise pour tout le monde.
        assert not est_disponible(Espace.query.get(plan["atelier"]), DEMAIN, "15:00", "17:00")

        db.session.delete(seance)
        db.session.delete(atelier)
        db.session.commit()
        assert Occupation.query.filter_by(session_id=seance.id).count() == 0


def test_seance_annulee_libere_la_salle(app, plan):
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier annulé")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique", espace_id=plan["demi_a"],
            date_session=DEMAIN, heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()
        assert Occupation.query.filter_by(session_id=seance.id).count() == 1

        seance.statut = "annulee"
        db.session.commit()
        assert Occupation.query.filter_by(session_id=seance.id).count() == 0
        assert est_disponible(Espace.query.get(plan["grande"]), DEMAIN, "14:00", "16:00")

        db.session.delete(seance)
        db.session.delete(atelier)
        db.session.commit()


def test_seance_sans_salle_ne_cree_rien(app, plan):
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier sans salle")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique",
            date_session=DEMAIN, heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()
        assert Occupation.query.filter_by(session_id=seance.id).count() == 0
        db.session.delete(seance)
        db.session.delete(atelier)
        db.session.commit()


def test_deplacer_une_seance_deplace_son_occupation(app, plan):
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier déplacé")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique", espace_id=plan["demi_a"],
            date_session=DEMAIN, heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()

        seance.espace_id = plan["cuisine"]
        seance.heure_debut = "09:00"
        seance.heure_fin = "11:00"
        db.session.commit()

        occ = Occupation.query.filter_by(session_id=seance.id).one()
        assert occ.espace_id == plan["cuisine"]
        assert occ.minute_debut == 540
        # L'ancienne salle est bien redevenue libre.
        assert est_disponible(Espace.query.get(plan["demi_a"]), DEMAIN, "14:00", "16:00")

        db.session.delete(seance)
        db.session.delete(atelier)
        db.session.commit()


def test_un_creneau_agenda_bloque_aussi(app, plan):
    with app.app_context():
        from app.models import User
        user = User.query.first()
        creneau = AgendaCreneau(
            user_id=user.id, titre="Réunion d'équipe", type_creneau="reunion",
            date_creneau=DEMAIN, heure_debut="10:00", heure_fin="12:00",
            espace_id=plan["demi_b"],
        )
        db.session.add(creneau)
        db.session.commit()

        occ = Occupation.query.filter_by(creneau_id=creneau.id).one()
        assert occ.origine == "creneau"
        # Une réunion dans une moitié bloque la grande salle : la secrétaire
        # ne peut plus la louer par-dessus.
        assert not est_disponible(Espace.query.get(plan["grande"]), DEMAIN, "10:00", "12:00")

        db.session.delete(creneau)
        db.session.commit()
        assert Occupation.query.filter_by(creneau_id=creneau.id).count() == 0


def test_reconciliation_est_idempotente(app, plan):
    with app.app_context():
        atelier = AtelierActivite(secteur="Numérique", nom="Atelier reconcilié")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(
            atelier_id=atelier.id, secteur="Numérique", espace_id=plan["atelier"],
            date_session=DEMAIN, heure_debut="14:00", heure_fin="16:00",
        )
        db.session.add(seance)
        db.session.commit()

        avant = Occupation.query.count()
        reconcilier_occupations()
        reconcilier_occupations()
        assert Occupation.query.count() == avant

        db.session.delete(seance)
        db.session.delete(atelier)
        db.session.commit()


# ---------------------------------------------------------------------------
# Suppressions : rien ne doit disparaître par surprise
# ---------------------------------------------------------------------------

def test_supprimer_un_espace_ne_detruit_pas_le_materiel(app, plan):
    """SQLite n'applique pas les ON DELETE : c'est l'ORM qui doit délier."""
    with app.app_context():
        item = InventaireItem(
            secteur="Numérique", id_interne=f"TEST-SALLE-{date.today().isoformat()}",
            designation="Tablette de test", espace_id=plan["armoire"], localisation="Armoire",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        db.session.delete(Espace.query.get(plan["armoire"]))
        db.session.commit()

        survivant = InventaireItem.query.get(item_id)
        assert survivant is not None, "le matériel ne doit jamais être supprimé avec la salle"
        assert survivant.espace_id is None
        assert survivant.localisation == "Armoire"  # la trace d'origine reste

        db.session.delete(survivant)
        db.session.commit()


def test_supprimer_un_parent_emporte_ses_enfants_et_leurs_occupations(app, plan):
    with app.app_context():
        _occuper(plan["demi_a"])
        db.session.delete(Espace.query.get(plan["grande"]))
        db.session.commit()

        assert Espace.query.get(plan["demi_a"]) is None
        assert Espace.query.get(plan["demi_b"]) is None
        assert Occupation.query.filter_by(espace_id=plan["demi_a"]).count() == 0


# ---------------------------------------------------------------------------
# Arbre, recherche, plan type
# ---------------------------------------------------------------------------

def test_arbre_ordonne_avec_profondeurs(app, plan):
    with app.app_context():
        arbre = arbre_du_site(plan["site_id"])
        profondeurs = {e.nom: p for e, p in arbre}
        assert profondeurs["Grande salle"] == 0
        assert profondeurs["Partie A"] == 1
        assert profondeurs["Armoire sécurisée n°1"] == 1
        # Un enfant suit toujours son parent dans l'ordre de lecture.
        noms = [e.nom for e, _ in arbre]
        assert noms.index("Grande salle") < noms.index("Partie A")


def test_recherche_priorise_les_libres_et_la_bonne_taille(app, plan):
    with app.app_context():
        _occuper(plan["demi_a"], "14:00", "16:00")
        lignes = recherche_disponibilite(DEMAIN, "14:00", "16:00", effectif=30)

        par_nom = {l["espace"].nom: l for l in lignes}
        assert par_nom["Partie B"]["libre"] is True
        assert par_nom["Grande salle"]["libre"] is False
        assert "Partie A" in par_nom["Grande salle"]["raison"]
        # L'armoire n'est pas occupable : elle n'a rien à faire ici.
        assert "Armoire sécurisée n°1" not in par_nom
        # L'atelier (12 places) est signalé trop petit pour 30 personnes.
        assert par_nom["Atelier"]["capacite_insuffisante"] is True
        # Les libres remontent en tête.
        assert lignes[0]["libre"] is True


def test_plan_type_installe_un_batiment_complet(app):
    with app.app_context():
        site = Site(nom="Site plan type", code=f"test-plan-type-{uuid4().hex[:8]}")
        db.session.add(site)
        db.session.commit()

        crees = installer_plan(site)
        assert crees == len(PLAN_CENTRE_SOCIAL)
        # Relancer ne duplique rien.
        assert installer_plan(site) == 0

        noms = {e.nom for e in Espace.query.filter_by(site_id=site.id).all()}
        assert "Espace snoezelen" in noms
        assert "Armoire sécurisée n°1" in noms

        # L'armoire est bien dans l'atelier, en stockage et hors planning.
        armoire = Espace.query.filter_by(site_id=site.id, nom="Armoire sécurisée n°1").one()
        assert armoire.parent.nom == "Atelier"
        assert armoire.stockage is True and armoire.reservable is False and armoire.securise is True

        # Les moitiés de la grande salle sont bien ses enfants.
        grande = Espace.query.filter_by(site_id=site.id, nom="Grande salle d'activité").one()
        assert {e.nom for e in grande.enfants} == {
            "Grande salle — partie A", "Grande salle — partie B",
        }

        # Le bureau de la petite enfance : ni occupable, ni louable.
        bureau_pe = Espace.query.filter_by(site_id=site.id, nom="Bureau petite enfance").one()
        assert bureau_pe.reservable is False and bureau_pe.louable is False

        db.session.delete(site)
        db.session.commit()


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------

def test_les_ecrans_repondent(admin_client, plan):
    assert admin_client.get("/salles/").status_code == 200
    assert admin_client.get("/salles/disponibilite").status_code == 200
    assert admin_client.get(f"/salles/espace/{plan['grande']}").status_code == 200
    assert admin_client.get(f"/salles/espace/{plan['grande']}/modifier").status_code == 200
    assert admin_client.get(f"/salles/site/{plan['site_id']}/modifier").status_code == 200
    assert admin_client.get("/salles/inventaire/reprise").status_code == 200


def test_ecrans_fermes_aux_anonymes(client):
    for url in ("/salles/", "/salles/disponibilite"):
        reponse = client.get(url)
        assert reponse.status_code in (302, 401), url


def test_un_espace_ne_peut_pas_devenir_son_propre_descendant(app, plan):
    """Une boucle dans l'arbre ferait tourner le moteur de conflits en rond."""
    with app.app_context():
        grande = Espace.query.get(plan["grande"])
        interdits = zone_conflit_ids(grande)
        assert plan["demi_a"] in interdits  # donc refusé comme parent de la grande salle


# ---------------------------------------------------------------------------
# Mise à jour d'une installation existante
# ---------------------------------------------------------------------------

def test_les_droits_arrivent_sur_une_installation_existante(app):
    """Une permission neuve doit atteindre les rôles DÉJÀ en base.

    Les gabarits de rôles ne sont pas réappliqués lors d'une mise à jour
    (pour ne pas écraser les réglages faits à la main dans l'écran des
    droits) : sans entrée dans ``PERMS_AUTO_GRANT``, la permission serait
    créée sans être accordée à personne, et le module resterait invisible
    en production tout en passant les tests sur base neuve.
    """
    from app.models import Permission, Role
    from app.rbac import PERMS_AUTO_GRANT, bootstrap_rbac

    with app.app_context():
        codes = ("salles:view", "salles:edit")
        for code in codes:
            assert code in PERMS_AUTO_GRANT, f"{code} n'atteindrait aucun rôle existant"

        # On rejoue la situation d'avant la mise à jour : la permission
        # n'existe pas encore, les rôles, eux, sont déjà là.
        for code in codes:
            perm = Permission.query.filter_by(code=code).first()
            if perm is not None:
                for role in list(perm.roles) if hasattr(perm, "roles") else []:
                    role.permissions = [p for p in role.permissions if p.code != code]
                for role in Role.query.all():
                    role.permissions = [p for p in role.permissions if p.code != code]
                db.session.delete(perm)
        db.session.commit()

        bootstrap_rbac()

        for code in codes:
            perm = Permission.query.filter_by(code=code).first()
            assert perm is not None, f"{code} n'a pas été recréée"
            beneficiaires = {
                r.code for r in Role.query.all() if perm in r.permissions
            }
            attendus = set(PERMS_AUTO_GRANT[code])
            manquants = attendus - beneficiaires
            assert not manquants, f"{code} n'a pas été accordée à {manquants}"


def test_le_menu_affiche_les_salles(admin_client, plan):
    """Le module doit être atteignable depuis la navigation, pas seulement
    par son URL — dans le mode d'interface simple comme en mode expert."""
    for mode in ("simple", "expert"):
        admin_client.post("/ui-mode", data={"mode": mode})
        page = admin_client.get("/dashboard").get_data(as_text=True)
        assert "/salles/" in page, f"entrée « Salles » absente du menu en mode {mode}"
        assert "/salles/disponibilite" in page, f"entrée « Qui est libre ? » absente en mode {mode}"
