"""Contrats du reverse-proxy Windows pour l'accès kiosque mobile."""

from desktop import runtime
import pytest


def test_caddy_expose_seulement_le_kiosque_sur_le_port_mobile(tmp_path):
    config = {
        "hostname": "centre-social",
        "https_port": 8443,
        "kiosk_http_port": 8080,
        "web_port": 18080,
    }
    (tmp_path / "runtime").mkdir()
    caddyfile = runtime.write_caddy(config, tmp_path)
    text = caddyfile.read_text(encoding="utf-8")

    assert "https://centre-social:8443" in text
    assert ":8080 {" in text
    assert "path /kiosk /kiosk/* /static /static/* /media/branding /media/branding/* /healthz" in text
    assert "reverse_proxy 127.0.0.1:18080" in text
    assert 'respond "Accès réservé à l\'émargement sur le réseau local." 403' in text


def test_activation_bloque_avant_les_taches_et_saisies(tmp_path):
    from flask import Flask
    app = Flask('activation-test')
    calls = []
    app.before_request(lambda: calls.append('traitement'))
    app.add_url_rule('/', endpoint='index', view_func=lambda: 'centre ouvert')
    app.add_url_rule('/healthz', endpoint='healthz', view_func=lambda: 'ok')
    marker = tmp_path / 'private/activation.pending'
    marker.parent.mkdir(); marker.write_text('1')
    runtime.protect_pending_activation(app, tmp_path)
    client = app.test_client()
    assert client.get('/').status_code == 503
    assert calls == []
    assert client.get('/healthz').status_code == 200
    marker.unlink(); calls.clear()
    assert client.get('/').status_code == 200
    assert calls == ['traitement']


def test_moteur_correspond_au_cluster_existant(tmp_path):
    assert runtime.postgres_bin(tmp_path) == runtime.INSTALL / 'postgresql18/bin'
    version = tmp_path / 'postgresql/PG_VERSION'
    version.parent.mkdir(); version.write_text('17\n')
    assert runtime.postgres_bin(tmp_path, {'db_major': 18}) == runtime.INSTALL / 'postgresql/bin'
    assert version.read_text() == '17\n'
    version.write_text('18\n')
    assert runtime.postgres_bin(tmp_path, {'db_major': 17}) == runtime.INSTALL / 'postgresql18/bin'
    version.write_text('19\n')
    with pytest.raises(RuntimeError, match='non prise en charge'):
        runtime.postgres_bin(tmp_path)


@pytest.mark.parametrize('source,target', [(100023,180006), (170011,180006), (180001,180006), (170011,170011)])
def test_reprise_versions_compatibles(source, target):
    from desktop.migration import validate_postgres_versions
    validate_postgres_versions(source, target)


@pytest.mark.parametrize('source,target', [(180001,170011), (190000,180006), (90624,180006), (180001,160000)])
def test_reprise_refuse_retrogradation_et_versions_inconnues(source, target):
    from desktop.migration import validate_postgres_versions, MigrationError
    with pytest.raises(MigrationError):
        validate_postgres_versions(source, target)


def test_connexion_source_ipv6_et_exclamation(tmp_path):
    from desktop.migration import read_source
    source = read_source(tmp_path, 'postgresql://postgres:secret!@[::1]:5432/erp_pedagogie')
    assert source['url'].host == '::1'
    assert source['url'].password == 'secret!'
    assert source['url'].database == 'erp_pedagogie'


@pytest.mark.parametrize("lan_ip,expected", [
    ("192.168.1.200", "https://centre-social:8443, https://192.168.1.200:8443 {"),
    ("127.0.0.1", "https://centre-social:8443 {"),
    ("", "https://centre-social:8443 {"),
    ("pas-une-ip", "https://centre-social:8443 {"),
])
def test_certificat_couvre_l_adresse_ip_du_reseau_local(tmp_path, lan_ip, expected):
    config = {"hostname": "centre-social", "https_port": 8443, "kiosk_http_port": 8082,
              "web_port": 18080, "lan_ip": lan_ip}
    text = runtime.write_caddy(config, tmp_path).read_text(encoding="utf-8")
    assert expected in text
