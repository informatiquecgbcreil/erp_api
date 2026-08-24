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

> **Important** : l'URL publique (`ERP_PUBLIC_BASE_URL`) doit être joignable
> par le navigateur de la personne au moment de la connexion (le retour OAuth
> est une simple redirection de navigateur — Google n'a pas besoin d'atteindre
> le serveur). En LAN, `http://erp-cgb:8000` fonctionne donc très bien.

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
