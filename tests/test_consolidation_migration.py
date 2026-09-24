"""Reprise : noms libres, fichiers complets et refus avant écriture."""
import json
from pathlib import Path
import zipfile

import pytest


def test_source_nom_base_libre_et_aucune_modification(tmp_path):
    from desktop.migration import read_source
    env = tmp_path / ".env"
    content = 'DATABASE_URL=postgresql://agent:secret@localhost:5432/erp_pedagogie\nAPP_UPLOAD_DIR=mes-pieces\nMAIL_HOST=smtp.test\nSECRET_KEY=ancien-secret\n'
    env.write_text(content)
    source = read_source(tmp_path)
    assert source['url'].database == 'erp_pedagogie'
    assert source['roots']['uploads'] == tmp_path / 'mes-pieces'
    assert source['settings'] == {'MAIL_HOST': 'smtp.test'}
    assert env.read_text() == content


def test_source_inconnue_refusee(tmp_path):
    from desktop.migration import read_source, MigrationError
    with pytest.raises(MigrationError):
        read_source(tmp_path, 'sqlite:///base.db')


def test_archive_conserve_instance_et_reste_portable(tmp_path):
    from app.services.instance_archive import create_archive, stage_archive, install_staged
    roots = {key: tmp_path / 'ancien' / key for key in ('instance', 'uploads')}
    for root in roots.values():
        root.mkdir(parents=True)
    (roots['instance'] / 'signatures_tmp').mkdir()
    (roots['instance'] / 'signatures_tmp' / 'preuve.png').write_bytes(b'signature')
    (roots['uploads'] / 'facture.pdf').write_bytes(b'facture')
    archive = tmp_path / 'lot.zip'
    create_archive(archive, roots)
    manifest = stage_archive(archive, tmp_path / 'stage')
    assert len(manifest['files']) == 2
    target = {key: tmp_path / 'nouveau' / key for key in roots}
    install_staged(tmp_path / 'stage', target)
    assert (target['instance'] / 'signatures_tmp' / 'preuve.png').read_bytes() == b'signature'
    assert (target['uploads'] / 'facture.pdf').read_bytes() == b'facture'


@pytest.mark.parametrize('name', ['../fuite', 'C:/fuite', 'a\\..\\fuite', 'a/../fuite', 'a./fuite'])
def test_archive_piegee_refusee_avant_extraction(tmp_path, name):
    from app.services.instance_archive import stage_archive
    archive = tmp_path / 'attaque.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('valide.txt', 'ok')
        z.writestr(name, 'interdit')
    with pytest.raises(RuntimeError):
        stage_archive(archive, tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()


def test_ancienne_archive_restauree(tmp_path):
    from app.services.instance_archive import stage_archive, install_staged
    archive = tmp_path / 'ancien.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('justifs/facture.pdf', 'preuve')
    assert stage_archive(archive, tmp_path / 'stage') is None
    install_staged(tmp_path / 'stage', {'uploads': tmp_path / 'uploads'})
    assert (tmp_path / 'uploads/justifs/facture.pdf').read_text() == 'preuve'


def test_chemins_windows_adaptes_sans_modifier_notes(tmp_path):
    from sqlalchemy import create_engine, text
    from app.services.instance_archive import remap_paths
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE document (id INTEGER PRIMARY KEY, file_path TEXT, note TEXT)'))
        conn.execute(text('INSERT INTO document VALUES (1, :path, :path)'), {'path': r'C:\Ancien\instance\passeport\p.pdf'})
        remap_paths(conn, {'instance': r'C:\Ancien\instance'}, {'instance': tmp_path})
        row = conn.execute(text('SELECT file_path, note FROM document')).one()
        assert row[0] == str(tmp_path / 'passeport/p.pdf')
        assert row[1] == r'C:\Ancien\instance\passeport\p.pdf'


def test_psql_annule_toute_la_transaction(app, tmp_path, monkeypatch):
    from app.services import sauvegarde as svc
    import subprocess
    calls = []
    monkeypatch.setattr(svc, '_trouver_psql', lambda: 'psql')
    def run(command, **kw):
        calls.append((command, kw))
        return subprocess.CompletedProcess(command, 1, b'', b'Erreur incluant des donnees privees')
    monkeypatch.setattr(svc.subprocess, 'run', run)
    with app.app_context(), pytest.raises(RuntimeError) as error:
        svc._restaurer_postgres(tmp_path / 'source.sql', 'postgresql://u:secret@localhost/db')
    command, arguments = calls[0]
    assert '--single-transaction' in command and 'ON_ERROR_STOP=1' in command and '-X' in command
    assert 'secret' not in ' '.join(command)
    assert arguments['env']['PGPASSWORD'] == 'secret'
    assert 'privees' not in str(error.value)


def test_tests_isoles_des_dossiers_de_deploiement(app):
    from tests.conftest import _TMP
    assert Path(app.instance_path).is_relative_to(_TMP)
    assert Path(app.config['BACKUP_DIR']).is_relative_to(_TMP)


def test_reprise_inachevee_ne_demarre_pas_le_web():
    from desktop.runtime import web
    with pytest.raises(RuntimeError, match='reprise'):
        web({'migration_source': 'ancien'})


def test_reprise_conserve_integrations_et_politique_de_retention(tmp_path):
    from desktop.migration import read_source
    (tmp_path / '.env').write_text('DATABASE_URL=postgresql://u:p@localhost/une_autre_base\n'
        'PORTAIL_TOKEN=jeton\nPROGRAMME_FTP_PASSWORD=secret\nPURGE_INACTIFS_AUTO=0\n'
        'LIBREOFFICE_PATH=C:/outils/soffice.exe\nPASSWORD_RESET_ALLOW_DEBUG_LINK=1\n')
    settings = read_source(tmp_path)['settings']
    assert settings['PORTAIL_TOKEN'] == 'jeton'
    assert settings['PROGRAMME_FTP_PASSWORD'] == 'secret'
    assert settings['PURGE_INACTIFS_AUTO'] == '0'
    assert settings['LIBREOFFICE_PATH'] == 'C:/outils/soffice.exe'
    assert 'PASSWORD_RESET_ALLOW_DEBUG_LINK' not in settings


def test_reprise_documents_retrouves_recopies_ou_comptes(tmp_path):
    """Cas réel : signatures enregistrées sous un ancien dossier, fichiers
    présents dans le dossier actuel, quelques pièces ailleurs, d'autres perdues."""
    from sqlalchemy import create_engine, text
    from desktop.migration import plan_documents, apply_document_plan
    from app.services.instance_archive import remap_paths
    old = tmp_path / 'AppGestion'; documents = old / 'instance'
    (documents / 'signatures_tmp').mkdir(parents=True)
    (documents / 'preuve.txt').write_text('preuve')
    (documents / 'signatures_tmp' / 'sig_1.png').write_bytes(b'png')
    ailleurs = tmp_path / 'Partage'; ailleurs.mkdir()
    (ailleurs / 'feuille.docx').write_bytes(b'docx')
    (ailleurs / 'systeme.dll').write_bytes(b'x')
    source = {'root': old, 'roots': {'instance': documents, 'uploads': old / 'uploads'}}
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE piece (id INTEGER PRIMARY KEY, file_path TEXT)'))
        rows = ['instance/preuve.txt',                                    # dans les dossiers
                r'C:\Users\infor\Desktop\ERP\instance\signatures_tmp\sig_1.png',  # dossier déplacé
                str(ailleurs / 'feuille.docx'),                           # externe lisible
                str(ailleurs / 'systeme.dll'),                            # format refusé
                r'C:\ancien\instance\signatures_tmp\perdue.png',      # perdu
                'instance/absente.txt']                                   # perdu
        for i, value in enumerate(rows, 1):
            conn.execute(text('INSERT INTO piece VALUES (:i, :v)'), {'i': i, 'v': value})
        plan = plan_documents(conn, source)
        assert len(plan['relocated']) == 1 and len(plan['external']) == 1
        assert plan['missing'] == {'piece.file_path': 3}
        new = {'instance': tmp_path / 'nouveau/instance', 'uploads': tmp_path / 'nouveau/uploads'}
        (new['instance'] / 'signatures_tmp').mkdir(parents=True)
        (new['instance'] / 'signatures_tmp' / 'sig_1.png').write_bytes(b'png')  # copié avec le dossier
        remap_paths(conn, {'instance': str(documents)}, new, source_directory=old)
        apply_document_plan(conn, plan, source['roots'], new)
        values = dict(conn.execute(text('SELECT id, file_path FROM piece')).all())
    assert values[1] == str(new['instance'] / 'preuve.txt')
    assert values[2] == str(new['instance'] / 'signatures_tmp' / 'sig_1.png')
    assert Path(values[3]).parent == new['instance'] / 'documents_repris' and Path(values[3]).read_bytes() == b'docx'
    assert values[4] == rows[3] and values[5] == rows[4]  # références conservées, rien de copié
    assert not any(p.suffix == '.dll' for p in (new['instance'] / 'documents_repris').iterdir())


def test_service_reprise_prepare_uniquement_la_base(tmp_path, monkeypatch):
    from desktop import runtime
    root = tmp_path
    for folder in ('runtime', 'logs', 'postgresql'):
        (root / folder).mkdir()
    monkeypatch.setattr(runtime, 'configure_environment', lambda c: root)
    monkeypatch.setattr(runtime, 'start_database', lambda c, r: None)
    monkeypatch.setattr(runtime, 'run_tool', lambda *a, **kw: None)
    def should_not_spawn(*args, **kw):
        raise AssertionError('Aucun web ni sauvegarde avant migration')
    monkeypatch.setattr(runtime, 'spawn', should_not_spawn)
    def request_stop(delay):
        assert (root / 'runtime/database.ready').exists()
        assert not (root / 'runtime/ready').exists()
        (root / 'runtime/stop').write_text('1')
    monkeypatch.setattr(runtime.time, 'sleep', request_stop)
    runtime.supervise({'migration_source': 'source', 'migration_done': False})
    assert not (root / 'runtime/database.ready').exists()


def test_psql_echec_reel_preserve_donnees_et_contraintes(app, tmp_path):
    import uuid
    from sqlalchemy import inspect, text
    from app.extensions import db
    from app.services.sauvegarde import _restaurer_postgres
    with app.app_context():
        uri = app.config['SQLALCHEMY_DATABASE_URI']
        if not uri.startswith('postgresql'):
            pytest.skip('Validation transactionnelle réelle dans le job PostgreSQL.')
        name = 'restore_probe_' + uuid.uuid4().hex
        with db.engine.begin() as conn:
            conn.execute(text(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY, valeur TEXT UNIQUE)'))
            conn.execute(text(f"INSERT INTO {name} VALUES (7, 'préservé')"))
        sql = tmp_path / 'restore_broken.sql'
        sql.write_text(f'DROP TABLE {name}; CREATE TABLE {name} (id INTEGER, valeur TEXT); SELECT 1/0;', encoding='utf-8')
        try:
            with pytest.raises(RuntimeError, match='annulée'):
                _restaurer_postgres(sql, uri)
            with db.engine.connect() as conn:
                assert conn.execute(text(f'SELECT * FROM {name}')).one() == (7, 'préservé')
                assert inspect(conn).get_pk_constraint(name)['constrained_columns'] == ['id']
                assert inspect(conn).get_unique_constraints(name)[0]['column_names'] == ['valeur']
        finally:
            with db.engine.begin() as conn:
                conn.execute(text(f'DROP TABLE IF EXISTS {name}'))


def test_restauration_cli_valide_archive_avant_demarrage(tmp_path, monkeypatch):
    from tools import restore_instance
    from config import Config
    import sys
    source = tmp_path / 'sauvegarde.db'; source.write_bytes(b'non lu avant le zip')
    archive = tmp_path / 'sauvegarde_uploads.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../fuite', 'interdit')
    monkeypatch.setattr(Config, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///' + str(tmp_path / 'cible.db'))
    monkeypatch.setattr(sys, 'argv', ['restore_instance.py', '--db', str(source), '--uploads', str(archive)])
    def forbidden():
        raise AssertionError('L’application et ses migrations ne doivent pas démarrer')
    monkeypatch.setattr(restore_instance, 'create_app', forbidden)
    with pytest.raises(RuntimeError, match='Archive refusée'):
        restore_instance.main()
    assert not (tmp_path / 'cible.db').exists()
