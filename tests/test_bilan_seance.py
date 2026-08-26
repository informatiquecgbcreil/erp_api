"""Bilan pédagogique de séance saisi depuis la page d'émargement.

Les colonnes existaient depuis longtemps sans écran de saisie ; ce bloc les
rend enfin remplissables, et le bilan remonte dans les descriptions d'agenda.
"""
import datetime as dt
import uuid


def _seance(app, *, nom, secteur):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    with app.app_context():
        at = AtelierActivite(nom=nom, secteur=secteur, type_atelier="COLLECTIF",
                             capacite_defaut=12, is_active=True)
        db.session.add(at)
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                            date_session=dt.date.today(), heure_debut="14:00",
                            heure_fin="16:00", capacite=12, statut="realisee")
        db.session.add(s)
        db.session.commit()
        return s.id


def test_bloc_bilan_present_sur_l_emargement(app, admin_client):
    tag = uuid.uuid4().hex[:6]
    sid = _seance(app, nom=f"Atelier{tag}", secteur=f"Num{tag}")

    r = admin_client.get(f"/activite/session/{sid}/emargement")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Bilan de la séance" in body
    for champ in ("intention_seance", "bilan_qualitatif", "pertinence",
                  "difficulte", "participation_groupe", "commentaire_pedagogique"):
        assert f'name="{champ}"' in body


def test_enregistrement_du_bilan(app, admin_client):
    from app.extensions import db
    from app.models import SessionActivite

    tag = uuid.uuid4().hex[:6]
    sid = _seance(app, nom=f"Atelier{tag}", secteur=f"Num{tag}")

    r = admin_client.post(f"/activite/session/{sid}/emargement", data={
        "action": "bilan_seance",
        "intention_seance": "Prendre en main la messagerie",
        "intention_seance_detail": "Boîte de réception, pièces jointes",
        "bilan_qualitatif": "Groupe très participatif.",
        "pertinence": "fort", "difficulte": "moyen", "participation_groupe": "fort",
        "a_reprendre": "1", "commentaire_pedagogique": "Prévoir un support papier.",
    })
    assert r.status_code == 302
    with app.app_context():
        s = db.session.get(SessionActivite, sid)
        assert s.intention_seance == "Prendre en main la messagerie"
        assert s.bilan_qualitatif == "Groupe très participatif."
        assert (s.pertinence, s.difficulte, s.participation_groupe) == ("fort", "moyen", "fort")
        assert s.a_reprendre is True
        assert s.commentaire_pedagogique == "Prévoir un support papier."

    # Décocher « à reprendre » doit bien le remettre à faux
    admin_client.post(f"/activite/session/{sid}/emargement", data={
        "action": "bilan_seance", "intention_seance": "Prendre en main la messagerie",
    })
    with app.app_context():
        assert db.session.get(SessionActivite, sid).a_reprendre is False


def test_valeur_hors_echelle_ignoree(app, admin_client):
    """Un ressenti fantaisiste ne doit pas entrer en base."""
    from app.extensions import db
    from app.models import SessionActivite

    tag = uuid.uuid4().hex[:6]
    sid = _seance(app, nom=f"Atelier{tag}", secteur=f"Num{tag}")

    admin_client.post(f"/activite/session/{sid}/emargement", data={
        "action": "bilan_seance", "intention_seance": "X", "pertinence": "excellentissime",
    })
    with app.app_context():
        assert db.session.get(SessionActivite, sid).pertinence is None


def test_bilan_remonte_dans_l_agenda(app, admin_client):
    """Boucle la demande d'origine : les observations saisies apparaissent
    dans la description de l'événement (flux iCal et synchro Google)."""
    from app.extensions import db
    from app.models import SessionActivite, User
    from app.services.calendrier import OPTIONS_DEFAUT, evenements_pour_periode

    tag = uuid.uuid4().hex[:6]
    sid = _seance(app, nom=f"Atelier{tag}", secteur=f"Num{tag}")
    admin_client.post(f"/activite/session/{sid}/emargement", data={
        "action": "bilan_seance",
        "intention_seance": f"Intention{tag}",
        "bilan_qualitatif": f"Observation{tag}",
    })

    with app.app_context():
        s = db.session.get(SessionActivite, sid)
        user = User.query.filter_by(email="admin@example.org").first()
        evenements = evenements_pour_periode(
            user, du=s.date_session, au=s.date_session, options=dict(OPTIONS_DEFAUT)
        )
    lignes = [ligne for e in evenements for ligne in e["lignes"]]
    assert any(f"Intention{tag}" in ligne for ligne in lignes)
    assert any(f"Observation{tag}" in ligne for ligne in lignes)


def test_script_de_repliage_servi(app, admin_client):
    """Le repliage est un enrichissement : il doit être servi, et la page
    doit rester complète sans lui."""
    tag = uuid.uuid4().hex[:6]
    sid = _seance(app, nom=f"Atelier{tag}", secteur=f"Num{tag}")

    body = admin_client.get(f"/activite/session/{sid}/emargement").get_data(as_text=True)
    assert "js/emargement-ui.js" in body
    # Les blocs restent dans le HTML : sans JavaScript, rien n'est masqué.
    assert "Enregistrer une présence" in body
    assert "Consommation estimée" in body

    r = admin_client.get("/static/js/emargement-ui.js")
    assert r.status_code == 200
    assert "repli-corps" in r.get_data(as_text=True)


def test_fenetres_masquees_sans_bootstrap(app, admin_client):
    """Le gabarit charge Bootstrap depuis un CDN : sans Internet, les
    fenêtres (une note rapide par participant) restaient affichées en pleine
    page. Le remplaçant local doit être servi et prévoir de les masquer."""
    tag = uuid.uuid4().hex[:6]
    sid = _seance(app, nom=f"Atelier{tag}", secteur=f"Num{tag}")

    body = admin_client.get(f"/activite/session/{sid}/emargement").get_data(as_text=True)
    assert "js/emargement-ui.js" in body
    assert ".sans-bootstrap .modal{ display:none; }" in body

    script = admin_client.get("/static/js/emargement-ui.js").get_data(as_text=True)
    # Prend la main uniquement si Bootstrap est absent
    assert "if (window.bootstrap && window.bootstrap.Modal) { return; }" in script
    for protocole in ('data-bs-toggle="modal"', 'data-bs-dismiss="modal"', "Escape"):
        assert protocole in script
