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
