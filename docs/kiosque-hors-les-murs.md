# Kiosque hors les murs — mode d'emploi

Objectif : permettre l'émargement au kiosque (téléphone/tablette) **en dehors
de la structure** (pied d'immeuble, sorties, activités délocalisées), alors que
le serveur reste sur le réseau local et n'est pas exposé à internet.

Principe : un **tunnel sortant** est installé une fois pour toutes sur le
serveur Windows. Il donne une adresse publique en HTTPS. Côté application, la
**façade kiosque** fait que, par cette adresse, SEUL l'émargement kiosque
répond : la page de connexion, les données et l'administration renvoient
« porte close ». L'ERP ne sort jamais du réseau local.

---

## « J'ai déjà WireGuard, ça ne suffit pas ? »

Non, et il ne faut pas le remplacer : les deux outils font des choses opposées.

- **WireGuard (votre VPN)** : un accès **privé** et total. La personne
  connectée entre virtuellement dans le réseau local et voit toute
  l'application. Parfait pour la direction ou l'admin en déplacement —
  mais il faut installer et configurer un client sur chaque appareil,
  et chaque appareil configuré a accès à tout.
- **Le tunnel kiosque** : un accès **public** et minuscule. N'importe quel
  téléphone avec le lien peut pointer les présences d'une séance (jeton +
  code PIN), et ne peut rien faire d'autre — la façade kiosque bloque tout
  le reste. Rien à installer côté bénévole.

Donner WireGuard à un bénévole reviendrait à lui donner les clés de tout le
bâtiment pour qu'il signe une feuille dans l'entrée. Gardez WireGuard pour
vous ; le tunnel, c'est pour eux.

Bon signe au passage : si WireGuard fonctionne, le serveur sait déjà établir
des connexions sortantes — le tunnel s'installera sans difficulté.

## Étape 1 — Choisir le tunnel

Deux solutions gratuites et sans ouverture de port sur la box :

| | Tailscale Funnel | Cloudflare Tunnel |
|---|---|---|
| Nom de domaine à acheter | Non (adresse en `*.ts.net`) | Oui (≈ 10 €/an) |
| Adresse obtenue | `gestion-cgb.tail1234.ts.net` | `kiosque.votre-domaine.fr` |
| Difficulté | Facile | Moyenne |
| Conseil | **Pour démarrer** | Si vous voulez une adresse « propre » |

## Étape 2A — Tailscale Funnel (recommandé pour démarrer)

Sur le serveur Windows (compte administrateur) :

1. Télécharger et installer Tailscale : https://tailscale.com/download/windows
2. Se connecter (créer un compte gratuit, par exemple avec l'adresse Google de
   la structure) : icône Tailscale → *Log in*.
3. Ouvrir une invite de commandes **en administrateur** et taper :

   ```
   tailscale funnel --bg 8000
   ```

   (remplacer `8000` par le port réel de l'application s'il est différent —
   celui de l'adresse `http://gestion.cgb:PORT`).
4. La commande affiche l'adresse publique, du type
   `https://NOM-DU-SERVEUR.tail1234.ts.net`. **La noter.**

## Étape 2B — Cloudflare Tunnel (si vous avez un domaine)

1. Créer un compte sur https://dash.cloudflare.com et y rattacher votre domaine.
2. Zero Trust → Networks → Tunnels → *Create a tunnel* (type Cloudflared),
   suivre l'installation Windows proposée (un service s'installe tout seul).
3. Dans *Public hostnames*, ajouter : `kiosque.votre-domaine.fr` →
   `http://localhost:8000` (le port de l'application).

## Étape 3 — Activer la façade kiosque dans l'application

Sur le serveur, ouvrir le fichier `.env` à la racine de l'application
(le créer s'il n'existe pas, à côté de `config.py`) et ajouter :

```
KIOSK_PUBLIC_HOST=NOM-DU-SERVEUR.tail1234.ts.net
```

(l'adresse notée à l'étape 2, **sans** `https://` ni port), puis **redémarrer
le service de l'application**.

C'est tout. À partir de là :

- les liens et QR codes du kiosque (page d'émargement d'une séance → bouton
  Kiosque) utilisent automatiquement l'adresse publique : ils fonctionnent
  dans les murs comme dehors ;
- par l'adresse publique, seules les pages `/kiosk` répondent ; tout le reste
  affiche « Cet accès sert uniquement à l'émargement » ;
- l'accès LAN habituel (`http://gestion.cgb`) ne change pas d'un poil.

## Étape 4 — Le circuit côté équipe

⚠️ **Point important** : le bouton qui ouvre le kiosque d'une séance (et donc
qui génère le lien + le QR) se trouve sur la page d'émargement de
l'application — une page réservée à l'équipe, donc **bloquée par la façade**
depuis l'extérieur, comme toutes les pages hors kiosque. Il faut donc
**ouvrir le kiosque AVANT de partir**, depuis un poste du centre (ou via
votre VPN WireGuard perso si vous êtes déjà dehors). Une fois ouvert, le
lien fonctionne ensuite sur n'importe quel téléphone, sans compte.

1. Avant la séance hors les murs, depuis le centre (ou en WireGuard),
   l'animateur ou l'accueil ouvre la séance dans l'application → bouton
   **Kiosque** → le lien + code PIN + QR s'affichent.
2. Envoyer le lien au bénévole/animateur (SMS, WhatsApp…) ou imprimer le QR.
3. Sur place : ouvrir le lien sur le téléphone, saisir le code PIN, faire
   pointer les participants. Aucun compte nécessaire, aucune autre page
   accessible.
4. Les présences arrivent en direct dans l'application : rien à ressaisir.

## Vérifier que la façade protège bien

Depuis un téléphone en 4G (hors du réseau de la structure, wifi ET
WireGuard coupés) :

- `https://ADRESSE-PUBLIQUE/kiosk/` (avec le « / » final) → doit afficher
  l'écran kiosque ✅
- `https://ADRESSE-PUBLIQUE/` (ou `/dashboard`) → doit afficher
  « Cet accès sert uniquement à l'émargement » ✅

Si le second test montre la page de connexion : la variable
`KIOSK_PUBLIC_HOST` est mal renseignée (vérifier l'orthographe exacte de
l'hôte, sans `https://`), ou le service n'a pas été redémarré.

**Si le premier test échoue** (le kiosque lui-même semble bloqué) :
vérifiez que vous avez bien tapé l'adresse en entier, avec `/kiosk/` — un
navigateur peut afficher l'adresse sans le « / » final, mais en la
retapant à la main il arrive de l'omettre, ce qui déclenchait ce même
blocage avant correction. Le lien généré par le bouton **Kiosque** de
l'application, lui, est toujours complet et ne pose pas ce problème —
en cas de doute, préférez toujours ce lien-là (ou le QR) à une adresse
retapée à la main.
