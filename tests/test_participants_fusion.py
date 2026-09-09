"""Fusion de doublons : aucun lien perdu, aucun périmètre franchi.

Fusionner supprime une fiche. Tout ce qui la désignait doit donc être
rattaché à la fiche conservée, pas laissé orphelin ni détruit par une
cascade. Le test principal énumère le schéma : une table ajoutée plus tard
sans être couverte fait échouer la suite.
"""
import datetime as dt
import uuid

import pytest


def _participant(app, **champs):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        suffixe = uuid.uuid4().hex[:6]
        p = Participant(nom=champs.pop("nom", "FUSION"), prenom=champs.pop("prenom", suffixe), **champs)
        db.session.add(p)
        db.session.commit()
        return p.id


def test_every_link_to_a_participant_is_transferable(app):
    """Le schéma est la référence : la fusion ne peut pas ignorer une table."""
    from app.extensions import db
    from app.models import Participant
    from app.participants.routes import _colonnes_vers_participant
    with app.app_context():
        attendues = {
            (table.name, colonne.name)
            for table in db.metadata.sorted_tables if table is not Participant.__table__
            for colonne in table.columns
            if any(fk.column is Participant.__table__.c.id for fk in colonne.foreign_keys)
        }
        couvertes = {(t.name, c.name) for t, c in _colonnes_vers_participant()}
        assert couvertes == attendues
        # Le module compte plus d'une vingtaine de liens : une liste écrite à la
        # main en oublierait, l'énumération du schéma non.
        assert len(couvertes) >= 20


def test_merging_moves_every_kind_of_link(app, admin_client):
    from app.extensions import db
    from app.models import (AtelierActivite, Cotisation, Participant, PasseportNote,
                            PresenceActivite, SessionActivite)
    garde, doublon = _participant(app), _participant(app)
    with app.app_context():
        atelier = AtelierActivite(nom=f"Fusion {uuid.uuid4().hex[:6]}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        seance = SessionActivite(atelier_id=atelier.id, secteur="Numérique",
                                 date_session=dt.date(2026, 3, 2))
        db.session.add(seance)
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=seance.id, participant_id=doublon),
            Cotisation(participant_id=doublon, annee_scolaire=2025,
                       type_cotisation="adhesion_individuelle", montant_du=10,
                       date_reference=dt.date(2025, 9, 1)),
            PasseportNote(participant_id=doublon, contenu="à conserver"),
        ])
        db.session.commit()
        seance_id = seance.id

    reponse = admin_client.post("/participants/merge", data={"keep_id": garde, "merge_ids": [doublon]})
    assert reponse.status_code == 302

    with app.app_context():
        assert db.session.get(Participant, doublon) is None
        assert db.session.get(Participant, garde) is not None
        # Présence, adhésion et note de passeport suivent la fiche conservée.
        assert PresenceActivite.query.filter_by(participant_id=garde, session_id=seance_id).count() == 1
        assert Cotisation.query.filter_by(participant_id=garde).count() == 1
        assert PasseportNote.query.filter_by(participant_id=garde).count() == 1
        # Rien n'est resté accroché à la fiche supprimée.
        assert Cotisation.query.filter_by(participant_id=doublon).count() == 0


def test_merging_drops_only_the_rows_that_would_collide(app, admin_client):
    from app.extensions import db
    from app.models import AtelierActivite, PresenceActivite, SessionActivite
    garde, doublon = _participant(app), _participant(app)
    with app.app_context():
        atelier = AtelierActivite(nom=f"Collision {uuid.uuid4().hex[:6]}", secteur="Numérique")
        db.session.add(atelier)
        db.session.flush()
        commune = SessionActivite(atelier_id=atelier.id, secteur="Numérique",
                                  date_session=dt.date(2026, 3, 3))
        propre = SessionActivite(atelier_id=atelier.id, secteur="Numérique",
                                 date_session=dt.date(2026, 3, 4))
        db.session.add_all([commune, propre])
        db.session.flush()
        db.session.add_all([
            PresenceActivite(session_id=commune.id, participant_id=garde),
            PresenceActivite(session_id=commune.id, participant_id=doublon),
            PresenceActivite(session_id=propre.id, participant_id=doublon),
        ])
        db.session.commit()
        ids = (commune.id, propre.id)

    assert admin_client.post("/participants/merge",
                             data={"keep_id": garde, "merge_ids": [doublon]}).status_code == 302
    with app.app_context():
        # La séance commune ne compte qu'une présence, l'autre est récupérée.
        assert PresenceActivite.query.filter_by(participant_id=garde, session_id=ids[0]).count() == 1
        assert PresenceActivite.query.filter_by(participant_id=garde, session_id=ids[1]).count() == 1
        assert PresenceActivite.query.filter_by(participant_id=doublon).count() == 0


def test_merging_many_duplicates_in_one_go(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    garde = _participant(app)
    doublons = [_participant(app) for _ in range(8)]
    reponse = admin_client.post("/participants/merge",
                                data={"keep_id": garde, "merge_ids": doublons})
    assert reponse.status_code == 302
    assert "/participants/duplicates" in reponse.headers["Location"]
    with app.app_context():
        assert Participant.query.filter(Participant.id.in_(doublons)).count() == 0
        assert db.session.get(Participant, garde) is not None


def test_merging_refuses_an_empty_or_self_only_selection(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    garde = _participant(app)
    for envoi in ({"keep_id": garde}, {"keep_id": garde, "merge_ids": [garde]}, {"merge_ids": [garde]}):
        reponse = admin_client.post("/participants/merge", data=envoi)
        assert reponse.status_code == 302
    with app.app_context():
        assert db.session.get(Participant, garde) is not None


def test_merging_refuses_a_fiche_outside_the_user_sector(app, admin_client, monkeypatch):
    """Le périmètre de la liste doit valoir pour l'action, pas seulement pour l'affichage.

    Aujourd'hui seuls des rôles globaux portent `participants:delete`, donc le
    garde-fou n'est atteignable qu'en simulant un rôle sectoriel — c'est bien
    lui qu'on teste, pas la matrice des rôles.
    """
    from app.extensions import db
    from app.models import Participant
    from app.participants import routes
    garde = _participant(app, created_secteur="Numérique")
    doublon = _participant(app, created_secteur="Familles")
    monkeypatch.setattr(routes, "_is_global_role", lambda: False)
    monkeypatch.setattr(routes, "_current_secteur", lambda: "Numérique")
    reponse = admin_client.post("/participants/merge", data={"keep_id": garde, "merge_ids": [doublon]})
    assert reponse.status_code == 403
    with app.app_context():
        assert db.session.get(Participant, doublon) is not None
        assert db.session.get(Participant, garde) is not None


def test_the_screen_offers_one_merge_per_group_not_per_pair(app, admin_client):
    """Vingt et une fiches d'une personne, c'est un geste, pas vingt."""
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        for _ in range(5):
            db.session.add(Participant(nom="ELBAYADTEST", prenom="Kamelia", created_secteur="EPE"))
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=certain").get_data(as_text=True)
    # Les cinq fiches tiennent dans un seul groupe, avec un seul jeu de contrôles.
    # (D'autres groupes de la base de test coexistent : on cible celui-ci.)
    assert page.count("<code>elbayadtest|kamelia</code>") == 1
    assert page.count("ELBAYADTEST") == 6  # l'en-tête du groupe, puis ses cinq lignes
    assert 'name="keep_id"' in page and 'name="merge_ids"' in page
    assert "Fusionner ce groupe" in page


def test_probable_matches_are_grouped_not_paired(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        for prenom in ("Muzeyyen", "Muzeyyene", "Muzeyen"):
            db.session.add(Participant(nom="ACARTEST", prenom=prenom, created_secteur="EPE"))
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=probable&t=0.8").get_data(as_text=True)
    # Les trois orthographes forment une chaîne : un seul groupe, un seul geste.
    # (D'autres groupes de la base de test coexistent : on cible celui-ci.)
    assert page.count("<code>acartest muzeyyen</code>") == 1
    assert page.count("ACARTEST") == 4  # l'en-tête du groupe, puis ses trois lignes


def test_merging_returns_to_the_duplicates_list_not_the_edit_page(app, admin_client):
    garde, doublon = _participant(app), _participant(app)
    reponse = admin_client.post("/participants/merge",
                                data={"keep_id": garde, "merge_ids": [doublon],
                                      "mode": "probable", "t": "0.8"})
    destination = reponse.headers["Location"]
    assert "/participants/duplicates" in destination
    assert "mode=probable" in destination
    # Le compte rendu nomme ce qui a bougé, il ne se contente pas de « fusion faite ».
    page = admin_client.get(destination).get_data(as_text=True)
    assert "a absorbé 1 fiche(s)" in page


def _bloc_du_groupe(page, cle):
    """Le fragment de page correspondant à un groupe, repéré par sa clé."""
    debut = page.index(f"<code>{cle}</code>")
    fin = page.find("</article>", debut)
    return page[debut:fin]


def test_a_family_sharing_a_phone_is_never_pre_checked(app, admin_client):
    """Une fratrie partage le téléphone des parents : ce n'est pas un doublon."""
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        for prenom in ("Jean", "Marie"):
            for _ in range(2):
                db.session.add(Participant(nom="FRATRIE", prenom=prenom,
                                           telephone="0344110022", created_secteur="EPE"))
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=certain").get_data(as_text=True)
    bloc = _bloc_du_groupe(page, "0344110022")
    assert "Rien n'est coché d'avance" in bloc
    assert "fratrie" in bloc
    assert "2 identités" in bloc
    # Chaque prénom garde sa propre fusion, avec ses deux fiches cochées.
    assert "Fusionner FRATRIE Jean" in bloc and "Fusionner FRATRIE Marie" in bloc
    assert bloc.count('type="checkbox" name="merge_ids"') > bloc.count('checked aria-label="Verser')


def test_a_couple_with_opposite_genders_is_never_pre_checked(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        db.session.add_all([
            Participant(nom="COUPLETEST", prenom="Jean", genre="Homme",
                        email="couple.test@example.org", created_secteur="EPE"),
            Participant(nom="COUPLETEST", prenom="Jeanne", genre="Femme",
                        email="couple.test@example.org", created_secteur="EPE"),
        ])
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=certain").get_data(as_text=True)
    bloc = _bloc_du_groupe(page, "couple.test@example.org")  # la vraie adresse, pas sa forme normalisée
    # Les prénoms se ressemblent, mais les genres s'opposent : deux personnes.
    assert "Rien n'est coché d'avance" in bloc
    assert "genres contradictoires" in bloc


def test_two_homonyms_born_on_different_days_are_never_pre_checked(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        db.session.add_all([
            Participant(nom="HOMONYMETEST", prenom="Paul", date_naissance=dt.date(1970, 4, 5),
                        created_secteur="EPE"),
            Participant(nom="HOMONYMETEST", prenom="Paul", date_naissance=dt.date(1995, 9, 1),
                        created_secteur="EPE"),
        ])
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=certain").get_data(as_text=True)
    bloc = _bloc_du_groupe(page, "homonymetest|paul")
    assert "Rien n'est coché d'avance" in bloc
    assert "Naissances ou genres contradictoires" in bloc


def test_spelling_variants_stay_a_single_pre_checked_merge(app, admin_client):
    """Le cas légitime ne doit pas payer le prix de la prudence."""
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        for prenom in ("Kadiatou", "Kadiattou"):
            db.session.add(Participant(nom="VARIANTETEST", prenom=prenom,
                                       telephone="0344110099", created_secteur="EPE"))
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=certain").get_data(as_text=True)
    bloc = _bloc_du_groupe(page, "0344110099")
    assert "Rien n'est coché d'avance" not in bloc
    assert "Fusionner ce groupe" in bloc


def test_searching_keeps_a_whole_family_in_view(app, admin_client):
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        for prenom in ("Alpha", "Beta", "Gamma"):
            for _ in range(2):
                db.session.add(Participant(nom="RECHERCHETEST", prenom=prenom, created_secteur="EPE"))
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=certain&q=recherchetest").get_data(as_text=True)
    assert "restreintes à « recherchetest »" in page
    # Les trois identités de la famille restent visibles ensemble.
    for prenom in ("Alpha", "Beta", "Gamma"):
        assert f"RECHERCHETEST {prenom}" in page


def test_merging_comes_back_to_the_same_family(app, admin_client):
    garde, doublon = _participant(app), _participant(app)
    reponse = admin_client.post("/participants/merge", data={
        "keep_id": garde, "merge_ids": [doublon], "mode": "certain",
        "t": "0.9", "q": "elbayad", "ancre": "groupe-nom-prenom-elbayad-kamelia"})
    destination = reponse.headers["Location"]
    assert "q=elbayad" in destination
    assert destination.endswith("#groupe-nom-prenom-elbayad-kamelia")


def test_probable_blocks_do_not_depend_on_row_order(app, admin_client):
    """La comparaison par blocs ne doit pas dépendre du contenu de la base."""
    from app.extensions import db
    from app.models import Participant
    with app.app_context():
        for prenom in ("Sylvianne", "Sylviane"):
            db.session.add(Participant(nom="BLOCTEST", prenom=prenom, created_secteur="EPE"))
        db.session.commit()
        # Vingt fiches sans rapport s'intercalent : avec une fenêtre glissante,
        # elles suffisaient à faire disparaître la paire.
        for indice in range(20):
            db.session.add(Participant(nom=f"BLOCTEST{indice}", prenom=f"Zzz{indice}",
                                       created_secteur="EPE"))
        db.session.commit()
    page = admin_client.get("/participants/duplicates?mode=probable&t=0.85").get_data(as_text=True)
    assert page.count("<code>bloctest sylvianne</code>") == 1
