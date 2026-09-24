import base64
from datetime import timedelta
from io import StringIO
import uuid

import pytest


@pytest.fixture
def scoped_people(app):
    from app.extensions import db
    from app.models import Participant, Role, User
    key = uuid.uuid4().hex
    with app.app_context():
        own = Participant(nom='Propre'+key, prenom='Test', created_secteur='Numérique')
        foreign = Participant(nom='Confidentiel'+key, prenom='Test', created_secteur='EPE', email=key+'@secret.test')
        users = []
        for code in ('animateur','responsable_secteur'):
            user = User(email=code+key+'@test.fr', nom=code, secteur_assigne='Numérique')
            user.set_password('motdepasse-tests')
            user.roles.append(Role.query.filter_by(code=code).one())
            db.session.add(user)
            users.append(user)
        db.session.add_all([own,foreign]); db.session.commit()
        return own.id, foreign.id, [u.email for u in users], foreign.nom


@pytest.mark.parametrize('role_index',[0,1])
def test_edition_et_export_hors_secteur_refuses(app, scoped_people, role_index):
    from app.models import Participant
    from app.extensions import db
    own, foreign, emails, name = scoped_people
    client = app.test_client()
    client.post('/',data={'email':emails[role_index],'password':'motdepasse-tests'})
    for url, data in [(f'/participants/{foreign}/edit',{'nom':'Pirate'}),
                      (f'/participants/{foreign}/date-naissance',{'date_naissance':'1990-01-01'}),
                      ('/participants/actions-groupees',{'action':'export','pid':[str(own),str(foreign)]})]:
        assert client.post(url,data=data).status_code == 403
    with app.app_context():
        assert db.session.get(Participant,foreign).nom == name
    assert client.get(f'/participants/{own}/edit').status_code == 200


def test_anonymisation_permission_distincte(app, scoped_people):
    own, foreign, emails, _ = scoped_people
    client = app.test_client()
    client.post('/',data={'email':emails[0],'password':'motdepasse-tests'})
    assert client.post(f'/participants/{own}/anonymize').status_code == 403
    assert client.post(f'/participants/{foreign}/anonymize').status_code == 403


def test_passeport_et_pieces_hors_secteur_refuses(app, scoped_people, tmp_path):
    from app.extensions import db
    from app.models import PasseportNote, PasseportPieceJointe
    own, foreign, emails, _ = scoped_people
    with app.app_context():
        note = PasseportNote(participant_id=own, secteur='EPE', contenu='SECRET-PASSEPORT-EPE')
        file = PasseportPieceJointe(participant_id=foreign, secteur='EPE', file_path=str(tmp_path/'secret'), original_name='secret.txt')
        db.session.add_all([note,file]); db.session.commit(); file_id = file.id
    client = app.test_client()
    client.post('/',data={'email':emails[0],'password':'motdepasse-tests'})
    assert client.get(f'/pedagogie/participant/{foreign}/passeport').status_code == 403
    assert client.get(f'/pedagogie/participant/{foreign}/passeport/file/{file_id}').status_code == 403
    page = client.get(f'/pedagogie/participant/{own}/passeport')
    assert page.status_code == 200
    assert b'SECRET-PASSEPORT-EPE' not in page.data


@pytest.mark.parametrize('host',['localhost','inconnu.test','kiosque.test.'])
def test_funnel_ne_devient_pas_interne_par_host(app, monkeypatch, host):
    monkeypatch.setitem(app.config,'KIOSK_PUBLIC_HOST','kiosque.test')
    client = app.test_client()
    response = client.get('/',headers={'Host':host,'Tailscale-Funnel-Request':'?1'})
    assert response.status_code == 403
    assert client.get('/kiosk/',headers={'Host':host,'Tailscale-Funnel-Request':'?1'}).status_code == 200


def test_kiosque_restreint_et_expire(app, scoped_people):
    from app.extensions import db
    from app.models import AtelierActivite, SessionActivite, PresenceActivite
    from app.utils.dates import utcnow
    own, foreign, _, secret_name = scoped_people
    token = uuid.uuid4().hex
    with app.app_context():
        atelier = AtelierActivite(nom='Session restreinte', secteur='Numérique')
        db.session.add(atelier); db.session.flush()
        s = SessionActivite(atelier_id=atelier.id, secteur='Numérique', kiosk_open=True,
                            kiosk_token=token, kiosk_opened_at=utcnow())
        db.session.add(s); db.session.commit(); sid=s.id
    client=app.test_client(); url=f'/kiosk/session/{token}'
    # « highlight » reste lié à la fiche créée par ce navigateur.
    assert secret_name.encode() not in client.get(url+f'?highlight={foreign}').data
    # Choix du centre : tout l'annuaire est retrouvable au kiosque (pas de fiche
    # en double pour un habitant connu d'un autre secteur), nom et prénom seuls.
    resultats = client.get(url+'/search',query_string={'q':secret_name.lower()}).get_json()['results']
    assert [r['id'] for r in resultats] == [foreign]
    assert '@' not in resultats[0]['label']
    client.post(url,data={'action':'emarger','participant_id':foreign})
    with app.app_context():
        assert PresenceActivite.query.filter_by(session_id=sid,participant_id=foreign).first()
        db.session.get(SessionActivite,sid).kiosk_opened_at=utcnow()-timedelta(hours=13)
        db.session.commit()
    assert client.get(url).status_code == 404


def test_csv_formules_neutralisees_sans_changer_les_montants():
    from app.utils import spreadsheet_csv as csv
    output=StringIO(); csv.writer(output).writerow(['=1+1','\t@SUM(1)',-12.5,'Texte'])
    assert next(csv.reader(StringIO(output.getvalue()))) == ["'=1+1","'\t@SUM(1)",'-12.5','Texte']


def test_signature_invalide_ne_cree_pas_de_fichier(tmp_path):
    from app.services.signatures import save_signature
    with pytest.raises(ValueError):
        save_signature('data:image/png;base64,'+base64.b64encode(b'pas une image').decode(),tmp_path,'sig')
    assert not list(tmp_path.iterdir())
