# Synchronisation Google Agenda en temps réel

L'application propose **deux** façons de retrouver les séances, deadlines et
événements dans un agenda personnel :

| | Flux iCal (existant) | Synchro push Google (cette page) |
|---|---|---|
| Mise à jour | quand Google/Apple relisent le lien (souvent **12–24 h**) | **immédiate** (à chaque création/modification) |
| Agendas compatibles | Google, Apple, Outlook… | Google Agenda uniquement |
| Configuration | aucune | identifiants OAuth à créer une fois (ci-dessous) |
| Sens | lecture seule | poussée par l'application (lecture seule côté Google) |

Les deux reposent sur les **mêmes réglages personnels** (page *Mon agenda* :
titre des événements, lignes de description, périmètre, fenêtre de jours).
Dans les deux cas, **aucun nom de participant ne sort** : seuls des agrégats
(nombre de présences saisies) apparaissent.

## Ce que fait la synchro push

- À la connexion, un **calendrier dédié** « Séances — \<nom de l'appli\> » est
  créé dans le compte Google de la personne — jamais dans son calendrier
  principal. Il se superpose dans Google Agenda et se masque d'un clic.
- Créer une séance dans l'application **réserve aussitôt le créneau** dans ce
  calendrier (date + heures ; journée entière si pas d'heure).
- La description de l'événement se met à jour toute seule au fil de l'eau :
  type de séance, horaire, **thématiques/modules travaillés**, capacité,
  **nombre de présences saisies** à l'émargement, secteur, lien direct vers la
  feuille d'émargement.
- Une séance **annulée** reste visible (préfixe « Annulée · ») mais libère le
  créneau (disponibilité « libre ») ; une séance à la corbeille disparaît.
- Une **empreinte** du contenu est mémorisée : si rien n'a changé, aucun appel
  à Google. Un **rattrapage automatique** (toutes les ~6 h) resynchronise tout
  ce qui aurait été manqué (coupure réseau, import Excel massif…).
- Déconnexion : le calendrier dédié est supprimé côté Google et l'accès révoqué.

## Configuration (une fois par installation)

Il faut créer des identifiants OAuth dans la console Google Cloud (gratuit) :

1. Ouvrir <https://console.cloud.google.com/> et créer un projet (ex. « ERP
   centre social »).
2. Menu **API et services → Bibliothèque** : activer **Google Calendar API**.
3. Menu **API et services → Écran de consentement OAuth** :
   - type « Externe » (comptes @gmail.com) ou « Interne » (Google Workspace) ;
   - renseigner nom de l'application et e-mail de contact ;
   - en mode « Externe », ajouter les adresses Google de l'équipe comme
     **utilisateurs test** (ou faire valider l'application par Google).
4. Menu **API et services → Identifiants → Créer des identifiants →
   ID client OAuth** :
   - type d'application : **Application Web** ;
   - **URI de redirection autorisés** : `<URL publique de l'ERP>/mon-agenda/google/retour`
     (l'URI exacte est affichée sur la page *Mon agenda* de l'application) ;
   - noter l'**ID client** et le **code secret**.
5. Dans le fichier `.env` de l'application :

   ```ini
   GOOGLE_OAUTH_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxx
   ```

6. Redémarrer le service. La carte « ⚡ Synchro Google Agenda en temps réel »
   de la page *Mon agenda* propose alors **Connecter mon agenda Google** à
   chaque membre de l'équipe.

### Mode « Test » vs « En production » (important)

Tant que l'application Google est en mode **Test** (statut par défaut du
type « Externe ») :

- seules les adresses ajoutées dans **Google Auth Platform → Audience →
  Utilisateurs test** peuvent se connecter — sinon Google affiche
  « Accès bloqué … Erreur 403 : access_denied » ;
- les jetons de rafraîchissement **expirent au bout de 7 jours** : la
  synchro s'arrête et chacun doit reconnecter son compte chaque semaine.

Une fois le fonctionnement validé, cliquer **« Publier l'application »**
(même écran Audience) pour passer **En production** : les jetons ne
expirent plus. Sans validation par Google, l'écran de connexion affiche
alors un avertissement « Google n'a pas validé cette application » —
cliquer *Paramètres avancés → Accéder à … (non sécurisé)* : c'est attendu
pour un outil interne, l'accès reste limité aux comptes qui se connectent
volontairement.

## Application locale / LAN : quelle URI de redirection ?

Le retour OAuth est une **simple redirection de navigateur** — les serveurs
de Google n'ont jamais besoin d'atteindre le vôtre. Une application 100 %
locale fonctionne donc. Mais Google impose des règles sur l'URI déclarée :

- en `http` (sans TLS), **seuls `localhost` et `127.0.0.1` sont acceptés**
  (n'importe quel port : `http://127.0.0.1:8000/...` est valide) ;
- une IP privée (`http://192.168.…`) est **refusée** par la console ;
- tout le reste doit être en `https` avec un vrai nom de domaine.

En pratique pour une installation LAN :

1. Gardez `ERP_PUBLIC_BASE_URL` sur l'adresse LAN (elle sert aux QR codes),
   et mettez dans `.env` :

   ```ini
   GOOGLE_OAUTH_REDIRECT_BASE=http://127.0.0.1:8000
   ```

   (le port où l'application écoute sur le serveur). La carte de la page
   *Mon agenda* affiche alors l'URI exacte à coller dans la console Google —
   la correspondance est stricte, **port compris**. La console accepte
   plusieurs URIs : déclarez-en une par port utilisé (ex. 5000 en debug,
   8000 en service).
2. Avec `127.0.0.1`, la connexion doit se faire depuis un navigateur ouvert
   **sur la machine serveur** (ou en bureau à distance). Ce n'est nécessaire
   qu'**une seule fois par personne** : chacun s'y connecte à l'ERP avec son
   compte, clique « Connecter mon agenda Google », s'authentifie avec son
   compte Google — ensuite la synchro tourne entièrement côté serveur, sans
   navigateur.
3. Pour permettre un jour la connexion depuis n'importe quel poste, il
   faudra une URL en `https` avec un nom de domaine (reverse proxy ou
   tunnel) — mais rien d'autre ne change.

## Notes techniques

- Aucune dépendance externe : OAuth 2.0 et l'API Calendar sont appelés en
  `urllib` (stdlib), TLS toujours vérifié (secours `certifi`), comme la
  veille financements.
- Jetons stockés en base (`google_agenda_compte`), révocables des deux côtés
  (bouton Déconnecter, ou <https://myaccount.google.com/permissions>).
- Identifiants d'événements **déterministes** (`erpseance<id>`) : rejouer une
  synchronisation ne crée jamais de doublon.
- La poussée se fait dans un **thread d'arrière-plan** après le commit : la
  saisie n'attend jamais Google.
- Service : `app/services/google_agenda.py` ; routes : `app/main/google_agenda.py` ;
  tests : `tests/test_google_agenda.py`.
