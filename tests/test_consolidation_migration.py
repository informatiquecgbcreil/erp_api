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
