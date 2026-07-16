"""Flux calendrier iCal (abonnement Google Agenda / Apple / Outlook)."""
import datetime as dt
import uuid

import pytest


def _login_role(app, email, role_code, secteur=None):
    from app.extensions import db
    from app.models import Role, User
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom=email.split("@")[0], secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role_code).first())
            db.session.add(u)
            db.session.commit()
    c = app.test_client()
    c.post("/", data={"email": email, "password": "pw-test-123"})
    return c


def _seance(app, *, secteur, nom, jour_offset=3, heure_debut="14:00", heure_fin="16:00", statut="realisee"):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    with app.app_context():
        at = AtelierActivite(nom=nom, secteur=secteur, type_atelier="COLLECTIF")
        db.session.add(at)
        db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=secteur, session_type="COLLECTIF",
                            date_session=dt.date.today() + dt.timedelta(days=jour_offset),
                            heure_debut=heure_debut, heure_fin=heure_fin, statut=statut)
        db.session.add(s)
        db.session.commit()
        return s.id


def _user_avec_token(app, email="agenda-user@example.org", secteur="Jeunesse", role="responsable_secteur"):
    from app.extensions import db
    from app.models import Role, User
    from app.services.calendrier import token_ou_creer
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, nom="AgendaUser", secteur_assigne=secteur)
            u.set_password("pw-test-123")
            u.roles.append(Role.query.filter_by(code=role).first())
            db.session.add(u)
            db.session.commit()
        token = token_ou_creer(u)
        return token


def test_flux_ics_public_par_jeton(app, client):
    suf = uuid.uuid4().hex[:6]
    sect = f"Jeun{suf}"
    _seance(app, secteur=sect, nom=f"Cours{suf}")
    token = _user_avec_token(app, email=f"a-{suf}@ex.org", secteur=sect)

    r = client.get(f"/calendrier/{token}.ics")
    assert r.status_code == 200
    assert r.mimetype == "text/calendar"
    body = r.get_data(as_text=True)
    assert "BEGIN:VCALENDAR" in body and "END:VCALENDAR" in body
    assert "BEGIN:VEVENT" in body
    assert f"Cours{suf}" in body
    # UID stable + horaire
    assert "UID:seance-" in body
    assert "DTSTART;TZID=Europe/Paris:" in body


def test_dtstamp_ics_utilise_l_heure_utc_partagee(app, client, monkeypatch):
    """Le DTSTAMP reste UTC et passe par le helper non déprécié du projet."""
    from app.services import calendrier

    instant_utc = dt.datetime(2026, 7, 10, 12, 34, 56)
    monkeypatch.setattr(calendrier, "utcnow", lambda: instant_utc)

    suf = uuid.uuid4().hex[:6]
    secteur = f"Stamp{suf}"
    _seance(app, secteur=secteur, nom=f"Cours{suf}")
    token = _user_avec_token(app, email=f"stamp-{suf}@ex.org", secteur=secteur)

    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert "DTSTAMP:20260710T123456Z" in body


def test_jeton_inconnu_404(app, client):
    assert client.get("/calendrier/nimportequoi.ics").status_code == 404


def test_flux_borne_au_secteur(app, client):
    suf = uuid.uuid4().hex[:6]
    mien, autre = f"Mien{suf}", f"Autre{suf}"
    _seance(app, secteur=mien, nom=f"Dedans{suf}")
    _seance(app, secteur=autre, nom=f"Dehors{suf}")
    token = _user_avec_token(app, email=f"borne-{suf}@ex.org", secteur=mien, role="responsable_secteur")

    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert f"Dedans{suf}" in body
    assert f"Dehors{suf}" not in body


def test_aucun_nom_de_participant_dans_le_flux(app, client):
    from app.extensions import db
    from app.models import Participant, PresenceActivite
    suf = uuid.uuid4().hex[:6]
    sect = f"Priv{suf}"
    sid = _seance(app, secteur=sect, nom=f"Atelier{suf}")
    with app.app_context():
        p = Participant(nom=f"SecretNom{suf}", prenom="Confidentiel")
        db.session.add(p)
        db.session.flush()
        db.session.add(PresenceActivite(session_id=sid, participant_id=p.id))
        db.session.commit()
    token = _user_avec_token(app, email=f"priv-{suf}@ex.org", secteur=sect)

    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert f"SecretNom{suf}" not in body
    assert "Confidentiel" not in body


def test_details_dans_l_evenement(app, client):
    """Description enrichie : type, horaire, capacité, présences (compteur),
    marqueur événement, + lien vers la séance. Toujours sans nom."""
    from app.extensions import db
    from app.models import AtelierActivite, Participant, PresenceActivite, SessionActivite
    import datetime as _dt
    suf = uuid.uuid4().hex[:6]
    sect = f"Det{suf}"
    with app.app_context():
        at = AtelierActivite(nom=f"CuisineDet{suf}", secteur=sect, type_atelier="COLLECTIF", capacite_defaut=12)
        db.session.add(at); db.session.flush()
        s = SessionActivite(atelier_id=at.id, secteur=sect, session_type="COLLECTIF",
                            date_session=_dt.date.today() + _dt.timedelta(days=4),
                            heure_debut="10:00", heure_fin="12:00", est_evenement=True)
        db.session.add(s); db.session.flush()
        # 2 présences (compteur) + une fiche au nom sensible
        for i in range(2):
            p = Participant(nom=f"NePasVoir{suf}{i}", prenom="X")
            db.session.add(p); db.session.flush()
            db.session.add(PresenceActivite(session_id=s.id, participant_id=p.id))
        db.session.commit()
        sid = s.id
    token = _user_avec_token(app, email=f"det-{suf}@ex.org", secteur=sect)

    raw = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    # Déplier (RFC 5545) avant d'asserter : les longues lignes sont repliées.
    body = raw.replace("\r\n ", "")
    assert f"🎉 CuisineDet{suf}" in body               # titre avec marqueur événement
    assert "Événement / temps fort" in body            # 1re ligne de la description
    assert "Horaire : 10:00" in body and "12:00" in body
    assert "Capacité : 12 places" in body
    assert "Présences saisies : 2" in body             # compteur, pas les noms
    assert f"/activite/session/{sid}/emargement" in body  # lien cliquable
    assert f"NePasVoir{suf}" not in body               # aucun nom exposé


def test_emargement_a_faire_sur_seance_passee_sans_presence(app, client):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    import datetime as _dt
    suf = uuid.uuid4().hex[:6]
    sect = f"Pass{suf}"
    with app.app_context():
        at = AtelierActivite(nom=f"Passe{suf}", secteur=sect, type_atelier="COLLECTIF")
        db.session.add(at); db.session.flush()
        db.session.add(SessionActivite(atelier_id=at.id, secteur=sect, session_type="COLLECTIF",
            date_session=_dt.date.today() - _dt.timedelta(days=3), heure_debut="14:00"))
        db.session.commit()
    token = _user_avec_token(app, email=f"pass-{suf}@ex.org", secteur=sect)
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert "Émargement à faire" in body


def test_seance_annulee_marquee_cancelled(app, client):
    suf = uuid.uuid4().hex[:6]
    sect = f"Ann{suf}"
    _seance(app, secteur=sect, nom=f"Annule{suf}", statut="annulee")
    token = _user_avec_token(app, email=f"ann-{suf}@ex.org", secteur=sect)
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert "STATUS:CANCELLED" in body


def test_seance_sans_heure_est_journee_entiere(app, client):
    suf = uuid.uuid4().hex[:6]
    sect = f"AllDay{suf}"
    _seance(app, secteur=sect, nom=f"JourEntier{suf}", heure_debut=None, heure_fin=None)
    token = _user_avec_token(app, email=f"allday-{suf}@ex.org", secteur=sect)
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert "DTSTART;VALUE=DATE:" in body


def test_page_mon_agenda_et_regeneration(app):
    from app.extensions import db
    from app.models import User
    c = _login_role(app, "agenda-page@example.org", "responsable_secteur", secteur="Jeunesse")
    r = c.get("/mon-agenda")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "/calendrier/" in body and ".ics" in body
    with app.app_context():
        t1 = User.query.filter_by(email="agenda-page@example.org").first().calendar_token
    assert t1

    # L'ancien lien fonctionne, puis on régénère → il cesse de fonctionner.
    assert c.get(f"/calendrier/{t1}.ics").status_code == 200
    c.post("/mon-agenda/regenerer")
    with app.app_context():
        t2 = User.query.filter_by(email="agenda-page@example.org").first().calendar_token
    assert t2 != t1
    assert c.get(f"/calendrier/{t1}.ics").status_code == 404
    assert c.get(f"/calendrier/{t2}.ics").status_code == 200


def test_facade_laisse_passer_le_flux_mais_bloque_le_reste(app, client):
    suf = uuid.uuid4().hex[:6]
    token = _user_avec_token(app, email=f"fac-{suf}@ex.org", secteur=f"F{suf}")
    app.config["KIOSK_PUBLIC_HOST"] = "public.exemple.fr"
    try:
        # Le flux iCal passe par l'hôte public…
        r = client.get(f"/calendrier/{token}.ics", headers={"Host": "public.exemple.fr"})
        assert r.status_code == 200
        # …mais le dashboard reste bloqué.
        r = client.get("/dashboard", headers={"Host": "public.exemple.fr"})
        assert r.status_code == 403
    finally:
        app.config["KIOSK_PUBLIC_HOST"] = ""


def test_formatage_ics_echappe_et_plie(app):
    """Nom long avec virgule/point-virgule : échappé et plié (lignes ≤ 75 o)."""
    from app.services.calendrier import _echapper, _plier
    assert _echapper("Atelier; cuisine, du soir") == "Atelier\\; cuisine\\, du soir"
    longue = "SUMMARY:" + "Atelier très long " * 8
    plie = _plier(longue)
    for i, ligne in enumerate(plie.split("\r\n")):
        # 1re ligne ≤ 75 octets ; les suivantes commencent par un espace.
        assert len(ligne.encode("utf-8")) <= 75
        if i > 0:
            assert ligne.startswith(" ")


def test_mon_agenda_exige_emargement_view(app):
    """Un rôle sans emargement:view n'accède pas à la page de synchro."""
    from app.extensions import db
    from app.models import Permission, Role
    with app.app_context():
        role = Role.query.filter_by(code="agenda_sans_droit").first()
        if role is None:
            role = Role(code="agenda_sans_droit", label="Sans agenda")
            db.session.add(role)
            perm = Permission.query.filter_by(code="dashboard:view").first()
            if perm:
                role.permissions.append(perm)
            db.session.commit()
    c = _login_role(app, "agenda-refuse@example.org", "agenda_sans_droit")
    assert c.get("/mon-agenda").status_code == 403


# ---------- Personnalisation, créneaux, préparation, export ----------

def _client_et_token(app, email, secteur, role="responsable_secteur"):
    c = _login_role(app, email, role, secteur=secteur)
    token = _user_avec_token(app, email=email, secteur=secteur, role=role)
    return c, token


def test_reglages_titre_et_champs_description(app, client):
    suf = uuid.uuid4().hex[:6]
    sect = f"Regl{suf}"
    _seance(app, secteur=sect, nom=f"Yoga{suf}")
    c, token = _client_et_token(app, f"regl-{suf}@ex.org", sect)

    # Titre personnalisé + description réduite au seul horaire
    r = c.post("/mon-agenda/preferences", data={
        "titre_format": "{secteur} · {atelier}",
        "champs_description": ["horaire"],
        "inclure_annulees": "1",
        "inclure_creneaux": "1",
        "preparation_minutes": "0", "jours_passe": "30", "jours_futur": "180",
    })
    assert r.status_code == 302

    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True).replace("\r\n ", "")
    assert f"SUMMARY:{sect} · Yoga{suf}" in body
    assert "Horaire : 14:00" in body
    assert "Capacité" not in body            # champ décoché
    assert "Secteur :" not in body           # champ décoché


def test_seances_annulees_exclues_si_option_decochee(app, client):
    suf = uuid.uuid4().hex[:6]
    sect = f"Excl{suf}"
    _seance(app, secteur=sect, nom=f"Annulee{suf}", statut="annulee")
    c, token = _client_et_token(app, f"excl-{suf}@ex.org", sect)

    c.post("/mon-agenda/preferences", data={
        "titre_format": "{atelier}", "champs_description": ["type"],
        # inclure_annulees ABSENT -> décoché
        "preparation_minutes": "0", "jours_passe": "30", "jours_futur": "180",
    })
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert f"Annulee{suf}" not in body


def test_creneaux_hors_ateliers_dans_le_flux(app, client):
    import datetime as _dt
    suf = uuid.uuid4().hex[:6]
    sect = f"Cren{suf}"
    c, token = _client_et_token(app, f"cren-{suf}@ex.org", sect)

    r = c.post("/mon-agenda/creneau", data={
        "type_creneau": "reunion", "titre": f"Équipe{suf}",
        "date_creneau": (_dt.date.today() + _dt.timedelta(days=2)).isoformat(),
        "heure_debut": "09:00", "heure_fin": "10:30",
        "description": "point hebdo", "repeter_semaines": "0",
    })
    assert r.status_code == 302
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True).replace("\r\n ", "")
    assert f"Réunion — Équipe{suf}" in body
    assert "point hebdo" in body

    # Un autre utilisateur ne voit PAS mes créneaux dans son flux.
    _, token_autre = _client_et_token(app, f"cren2-{suf}@ex.org", sect)
    body_autre = client.get(f"/calendrier/{token_autre}.ics").get_data(as_text=True)
    assert f"Équipe{suf}" not in body_autre


def test_creneau_repetition_hebdomadaire(app):
    import datetime as _dt
    from app.models import AgendaCreneau, User
    suf = uuid.uuid4().hex[:6]
    c = _login_role(app, f"rep-{suf}@ex.org", "responsable_secteur", secteur=f"R{suf}")
    d0 = _dt.date.today() + _dt.timedelta(days=1)
    c.post("/mon-agenda/creneau", data={
        "type_creneau": "preparation", "titre": f"Prep{suf}",
        "date_creneau": d0.isoformat(), "repeter_semaines": "3",
    })
    with app.app_context():
        uid = User.query.filter_by(email=f"rep-{suf}@ex.org").first().id
        rows = AgendaCreneau.query.filter_by(user_id=uid, titre=f"Prep{suf}").order_by(AgendaCreneau.date_creneau).all()
        assert len(rows) == 4  # occurrence initiale + 3 répétitions
        assert rows[1].date_creneau == d0 + _dt.timedelta(weeks=1)
        assert rows[3].date_creneau == d0 + _dt.timedelta(weeks=3)


def test_preparation_automatique_avant_seance(app, client):
    suf = uuid.uuid4().hex[:6]
    sect = f"Prep{suf}"
    _seance(app, secteur=sect, nom=f"Cuisine{suf}", heure_debut="14:00", heure_fin="16:00")
    c, token = _client_et_token(app, f"prep-{suf}@ex.org", sect)

    c.post("/mon-agenda/preferences", data={
        "titre_format": "{atelier}", "champs_description": ["type"],
        "inclure_annulees": "1", "preparation_minutes": "30",
        "jours_passe": "30", "jours_futur": "180",
    })
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True).replace("\r\n ", "")
    assert f"Préparation — Cuisine{suf}" in body
    assert "T133000" in body      # début de préparation 13:30
    assert "-prep@" in body       # UID distinct de la séance


def test_evenements_globaux_tous_secteurs(app, client):
    import datetime as _dt
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite
    suf = uuid.uuid4().hex[:6]
    mien, autre = f"Mien{suf}", f"Autre{suf}"
    with app.app_context():
        at = AtelierActivite(nom=f"FeteQuartier{suf}", secteur=autre, type_atelier="COLLECTIF")
        db.session.add(at); db.session.flush()
        db.session.add(SessionActivite(atelier_id=at.id, secteur=autre, session_type="COLLECTIF",
            date_session=_dt.date.today() + _dt.timedelta(days=5), est_evenement=True))
        # séance ordinaire d'un autre secteur : ne doit JAMAIS apparaître
        at2 = AtelierActivite(nom=f"Ordinaire{suf}", secteur=autre, type_atelier="COLLECTIF")
        db.session.add(at2); db.session.flush()
        db.session.add(SessionActivite(atelier_id=at2.id, secteur=autre, session_type="COLLECTIF",
            date_session=_dt.date.today() + _dt.timedelta(days=5)))
        db.session.commit()
    c, token = _client_et_token(app, f"glob-{suf}@ex.org", mien)

    # Sans l'option : rien de l'autre secteur.
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True)
    assert f"FeteQuartier{suf}" not in body

    # Avec l'option : l'événement oui, la séance ordinaire non.
    c.post("/mon-agenda/preferences", data={
        "titre_format": "{atelier}", "champs_description": ["type"],
        "inclure_annulees": "1", "evenements_tous_secteurs": "1",
        "preparation_minutes": "0", "jours_passe": "30", "jours_futur": "180",
    })
    body = client.get(f"/calendrier/{token}.ics").get_data(as_text=True).replace("\r\n ", "")
    assert f"FeteQuartier{suf}" in body
    assert f"Ordinaire{suf}" not in body


def test_export_ponctuel_par_periode(app):
    import datetime as _dt
    suf = uuid.uuid4().hex[:6]
    sect = f"Exp{suf}"
    # Une séance dans la période, une hors période.
    _seance(app, secteur=sect, nom=f"Dedans{suf}", jour_offset=10)
    _seance(app, secteur=sect, nom=f"Dehors{suf}", jour_offset=40)
    c, _ = _client_et_token(app, f"exp-{suf}@ex.org", sect)

    du = (_dt.date.today() + _dt.timedelta(days=5)).isoformat()
    au = (_dt.date.today() + _dt.timedelta(days=15)).isoformat()
    r = c.get(f"/mon-agenda/export.ics?du={du}&au={au}")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    body = r.get_data(as_text=True)
    assert f"Dedans{suf}" in body
    assert f"Dehors{suf}" not in body

    # Période invalide -> retour à la page avec message.
    r = c.get(f"/mon-agenda/export.ics?du={au}&au={du}")
    assert r.status_code == 302
