# Kiosque hors les murs

Ce guide concerne la distribution Windows consolidée. Le tunnel donne une
adresse HTTPS publique au **port réservé au kiosque**. Ce port est filtré par
Caddy avant d'atteindre l'application. Ne publiez ni le port interne de Waitress,
ni l'adresse HTTPS de l'administration.

Le VPN de l'équipe reste utilisable pour l'administration avec les droits du
compte connecté. Le kiosque public permet l'émargement sans compte salarié ;
son lien est un accès à la séance, à transmettre uniquement aux personnes concernées.

## 1. Vérifier le point d'entrée local

Choisir le mode réseau dans l'installateur. Le dossier de données contient
`public/kiosk-url.txt`, par exemple `http://192.168.1.20:8080`.
Relever son **port**. Sur le serveur, vérifier que
`http://127.0.0.1:8080/kiosk/` répond et que
`http://127.0.0.1:8080/` renvoie **403**. Adapter `8080` au port indiqué.

Une installation artisanale doit d'abord disposer d'un proxy avec cette même
liste de chemins autorisés. La variable `KIOSK_PUBLIC_HOST` seule ne remplace
pas le filtrage du port public. Le modèle est dans `desktop/runtime.py`.

## 2. Relier le tunnel à ce port

Après installation et connexion de Tailscale sur le serveur, exemple pour le
port kiosque `8080` :

```powershell
tailscale funnel --bg http://127.0.0.1:8080
tailscale funnel status
```

Le statut doit désigner cette destination locale. Si un ancien Funnel publiait
le port général de l'application, le retirer avant de configurer le kiosque.
`tailscale funnel reset` retire toutes les publications Funnel de cette machine :
vérifier auparavant qu'elles ne servent pas à un autre usage.
La syntaxe et les prérequis sont documentés dans la
[référence officielle Tailscale](https://tailscale.com/docs/reference/tailscale-cli/funnel).
Ces commandes doivent être vérifiées sur le serveur ; la CI de l'ERP ne se
connecte pas à un véritable compte Tailscale.

Avec Cloudflare Tunnel, le service HTTP de destination doit également être
`http://127.0.0.1:8080`, avec le port kiosque réel. Le fournisseur du tunnel et son
nom public sont indépendants de la base de données et du nom de la structure.

## 3. Générer les QR codes avec l'adresse publique

Renseigner le nom DNS public, sans protocole ni port :

```dotenv
KIOSK_PUBLIC_HOST=nom-du-serveur.tail1234.ts.net
```

- **Reprise d'une ancienne installation :** renseigner cette valeur dans son
  `.env` avant la reprise. L'assistant la conserve dans sa configuration protégée.
- **Installation neuve :** renseigner cette ligne dans le `.env` du dossier
  `application` installé sous `Program Files/Mon Centre Social`, avec les droits
  administrateur, puis redémarrer le service. Les variables reprises dans la
  configuration protégée sont prioritaires sur ce fichier : cette méthode ne
  remplace pas une valeur déjà importée.

Les liens et QR codes utilisent ensuite le nom public. Le dossier de données,
les ports locaux et l'accès de l'équipe restent ceux indiqués par l'assistant.

## 4. Utiliser le kiosque

Depuis l'ERP sur le réseau du centre ou via son VPN, ouvrir le kiosque de la
séance avant de partir. Partager le lien ou le QR avec la personne chargée de
l'émargement. Le lien contient un jeton secret et ouvre directement la séance ;
le PIN permet d'y accéder depuis l'accueil du kiosque. Ce ne sont pas deux
facteurs d'authentification cumulés.

Les nouvelles ouvertures ont un PIN de six chiffres et expirent après douze
heures. Les anciens PIN à quatre chiffres restent acceptés pendant leur période
d'ouverture. Refermer le kiosque après la séance. La recherche et l'émargement
sont limités au secteur de la séance ou aux inscriptions de son atelier.

## 5. Contrôler depuis l'extérieur

Sur un téléphone hors Wi-Fi et hors VPN :

- `/kiosk/` affiche l'accueil ; un lien de séance ouverte permet de pointer.
- `/`, `/dashboard` et `/admin/users` doivent répondre **403**.
- Le logo public, les fichiers statiques nécessaires et `/sources` restent
  accessibles. Les justificatifs, signatures et documents individuels ne le sont pas.

Si l'écran de connexion de l'ERP apparaît, couper la publication du tunnel et
corriger sa destination avant de le rouvrir. Un simple test avec le bon nom DNS
ne remplace pas le filtrage du port kiosque : le client peut changer son en-tête
`Host`. Les tests automatisés couvrent ce cas, le point final dans le nom et
l'en-tête ajouté par Tailscale Funnel.

La limitation de débit utilise l'adresse vue par l'application. Selon le tunnel,
plusieurs visiteurs peuvent partager cette adresse et donc le même quota. Les
quotas freinent les abus mais ne constituent pas une protection individuelle
contre le blocage par un autre visiteur.
