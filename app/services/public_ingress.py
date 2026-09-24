"""Identification restrictive de la façade publique.

Deux étages :

1. Les tunnels posent eux-mêmes un en-tête que le visiteur ne peut ni retirer
   ni imiter à distance. Tailscale Funnel (``Tailscale-Funnel-Request``) :
   toujours public. Cloudflare (``Cf-Connecting-Ip``) : public quand la façade
   est configurée (cet en-tête accompagne aussi un site entièrement servi par
   Cloudflare, qui n'a alors pas de kiosque « hors les murs »). L'ajouter soi-
   même depuis le réseau local ne fait que restreindre sa propre requête.
2. Sinon, quand ``KIOSK_PUBLIC_HOST`` est configuré, l'hôte demandé décide :
   l'hôte public, un hôte vide ou un nom inconnu sont traités comme publics
   (fermé par défaut). Restent internes : l'hôte de ERP_PUBLIC_BASE_URL, les
   noms déclarés (TRUSTED_HOSTS, ERP_LAN_HOSTS), le nom de ce serveur, les
   adresses IP privées et les noms en .local/.lan/.home.arpa/.internal.
   L'en-tête Host restant choisi par le client, ce second étage ne remplace
   jamais le filtrage du port public.

Le port public du proxy Windows reste filtré indépendamment de ces contrôles.
"""
import ipaddress
import socket
from urllib.parse import urlsplit
from flask import current_app, request

LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def hostname(value):
    try:
        return (urlsplit(value if "://" in value else "//" + value).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _machine_names():
    names = set()
    try:
        full = socket.gethostname().lower()
        names.update({full, full.split(".")[0]})
    except OSError:
        pass
    return {n for n in names if n}


def _is_lan_name(host):
    """Adresse privée, nom de ce serveur ou nom de domaine local."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in _machine_names() or host.endswith(LAN_SUFFIXES)
    # Tailnet (100.64.0.0/10) : l'équipe via VPN. Un accès Funnel porte toujours
    # son en-tête et a déjà été classé public.
    in_tailnet = address.version == 4 and address in _TAILNET
    return address.is_private or address.is_loopback or address.is_link_local or in_tailnet


def is_public_ingress():
    if "Tailscale-Funnel-Request" in request.headers:
        return True
    public = hostname(current_app.config.get("KIOSK_PUBLIC_HOST") or "")
    if not public:
        return False
    if "Cf-Connecting-Ip" in request.headers:
        return True
    incoming = hostname(request.host or "")
    if incoming == public or not incoming:
        return True
    known = {hostname(current_app.config.get("PUBLIC_BASE_URL") or ""), "localhost"}
    known.update(hostname(h) for h in current_app.config.get("TRUSTED_HOSTS") or [])
    known.update(hostname(h) for h in current_app.config.get("LAN_HOSTS") or [])
    known.discard("")
    if incoming in known:
        return False
    # Un nom de domaine inconnu ne transforme pas une requête en accès interne.
    return not _is_lan_name(incoming)
