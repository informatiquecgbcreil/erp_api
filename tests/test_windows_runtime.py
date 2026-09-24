"""Contrats du reverse-proxy Windows pour l'accès kiosque mobile."""

from desktop import runtime


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
