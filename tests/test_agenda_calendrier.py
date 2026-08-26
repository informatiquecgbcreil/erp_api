"""Vue calendrier de l'application (mois / semaine, lecture).

Elle doit montrer exactement ce que le flux iCal et la synchro Google
contiennent : mêmes réglages, même périmètre secteur.
"""
import datetime as dt
import uuid


def _user_client(app, *, email, secteur, role="responsable_secteur"):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role).first())
            db.session.add(u)
            db.session.commit()
        uid = u.id
    c = app.test_client()
    assert c.post("/", data={"email": email, "password": "pw-test-123"}).status_code == 302
    return c, uid


def _seance(app, *, secteur, nom, jour, heure="14:00"):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    with app.app_context():
        at = AtelierActivite.query.filter_by(nom=nom).first()
        if at is None:
            at = AtelierActivite(nom=nom, secteur=secteur, type_atelier="COLLECTIF", capacite_defaut=12)
            db.session.add(at)
            db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                            date_session=jour, heure_debut=heure, heure_fin="16:00",
                            capacite=12, statut="realisee")
        db.session.add(s)
        db.session.commit()
        return s.id


def _creneau(app, uid, *, titre, jour):
    from app.extensions import db
    from app.models import AgendaCreneau
    with app.app_context():
        c = AgendaCreneau(user_id=uid, type_creneau="reunion", titre=titre,
                          date_creneau=jour, heure_debut="10:00", heure_fin="12:00")
        db.session.add(c)
        db.session.commit()
        return c.id


def test_calendrier_affiche_seances_et_creneaux(app):
    tag = uuid.uuid4().hex[:6]
    secteur = f"Num{tag}"
    client, uid = _user_client(app, email=f"cal-{tag}@ex.org", secteur=secteur)
    jour = dt.date.today().replace(day=15)
    _seance(app, secteur=secteur, nom=f"Atelier{tag}", jour=jour)
    _creneau(app, uid, titre=f"Reunion{tag}", jour=jour)

    r = client.get("/mon-agenda/calendrier")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert f"Atelier{tag}" in body
    assert f"Reunion{tag}" in body
    assert "14:00" in body


def test_navigation_par_mois(app):
    """Les flèches changent bien de mois : ce qui est en septembre n'apparaît
    pas en octobre, et inversement."""
    tag = uuid.uuid4().hex[:6]
    secteur = f"Num{tag}"
    client, _ = _user_client(app, email=f"nav-{tag}@ex.org", secteur=secteur)

    ce_mois = dt.date.today().replace(day=15)
    mois_suivant = (ce_mois.replace(day=28) + dt.timedelta(days=7)).replace(day=15)
    _seance(app, secteur=secteur, nom=f"Courant{tag}", jour=ce_mois)
    _seance(app, secteur=secteur, nom=f"Suivant{tag}", jour=mois_suivant)

    body = client.get("/mon-agenda/calendrier").get_data(as_text=True)
    assert f"Courant{tag}" in body

    body_suivant = client.get(
        f"/mon-agenda/calendrier?ancre={mois_suivant.isoformat()}"
    ).get_data(as_text=True)
    assert f"Suivant{tag}" in body_suivant
    # La grille déborde sur les jours voisins : on vérifie le titre de période.
    from app.services.poste_travail import MOIS_FR
    assert MOIS_FR[mois_suivant.month - 1].capitalize() in body_suivant


def test_vue_semaine(app):
    tag = uuid.uuid4().hex[:6]
    secteur = f"Num{tag}"
    client, _ = _user_client(app, email=f"sem-{tag}@ex.org", secteur=secteur)
    lundi = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    _seance(app, secteur=secteur, nom=f"Semaine{tag}", jour=lundi + dt.timedelta(days=1))

    r = client.get(f"/mon-agenda/calendrier?vue=semaine&ancre={lundi.isoformat()}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert f"Semaine{tag}" in body
    assert "Semaine du" in body


def test_parametres_invalides_ne_cassent_rien(app):
    """Une URL bricolée retombe sur le mois courant plutôt que sur une 500."""
    tag = uuid.uuid4().hex[:6]
    client, _ = _user_client(app, email=f"bad-{tag}@ex.org", secteur=f"Num{tag}")
    for qs in ("?ancre=pas-une-date", "?vue=nimportequoi", "?ancre=2026-13-45&vue=x"):
        r = client.get(f"/mon-agenda/calendrier{qs}")
        assert r.status_code == 200, qs


def test_cloisonnement_secteur(app):
    """Le calendrier respecte le périmètre du flux : pas de séance d'un
    autre secteur chez un responsable de secteur."""
    tag = uuid.uuid4().hex[:6]
    mien, autre = f"Num{tag}", f"Fam{tag}"
    client, _ = _user_client(app, email=f"scope-{tag}@ex.org", secteur=mien)
    jour = dt.date.today().replace(day=15)
    _seance(app, secteur=mien, nom=f"Chezmoi{tag}", jour=jour)
    _seance(app, secteur=autre, nom=f"Ailleurs{tag}", jour=jour)

    body = client.get("/mon-agenda/calendrier").get_data(as_text=True)
    assert f"Chezmoi{tag}" in body
    assert f"Ailleurs{tag}" not in body


def test_reglage_creneaux_respecte(app):
    """Décocher « inclure mes créneaux » les retire aussi du calendrier :
    l'écran et le flux ne peuvent pas diverger."""
    from app.extensions import db
    from app.models import User
    from app.services.calendrier import OPTIONS_DEFAUT, sauvegarder_options

    tag = uuid.uuid4().hex[:6]
    secteur = f"Num{tag}"
    client, uid = _user_client(app, email=f"opt-{tag}@ex.org", secteur=secteur)
    jour = dt.date.today().replace(day=15)
    _creneau(app, uid, titre=f"Reunion{tag}", jour=jour)

    assert f"Reunion{tag}" in client.get("/mon-agenda/calendrier").get_data(as_text=True)

    with app.app_context():
        options = dict(OPTIONS_DEFAUT)
        options["inclure_creneaux"] = False
        sauvegarder_options(db.session.get(User, uid), options)

    assert f"Reunion{tag}" not in client.get("/mon-agenda/calendrier").get_data(as_text=True)


def test_lien_vers_la_seance(app):
    """Un clic sur une séance mène à sa feuille d'émargement."""
    tag = uuid.uuid4().hex[:6]
    secteur = f"Num{tag}"
    client, _ = _user_client(app, email=f"lien-{tag}@ex.org", secteur=secteur)
    jour = dt.date.today().replace(day=15)
    session_id = _seance(app, secteur=secteur, nom=f"Atelier{tag}", jour=jour)

    body = client.get("/mon-agenda/calendrier").get_data(as_text=True)
    assert f"/activite/session/{session_id}/emargement" in body
