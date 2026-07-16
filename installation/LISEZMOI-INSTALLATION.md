# Installation de l'application de gestion

Ce guide s'adresse à une personne **sans connaissances en informatique**.
Comptez 10 à 20 minutes. Il vous faut :

- un ordinateur sous **Windows 10 ou 11** (ou Windows Server 2019+),
- une **connexion internet** pendant l'installation,
- les **droits administrateur** sur l'ordinateur (Windows vous demandera
  une confirmation, cliquez « Oui »).

---

## Étape 1 — Récupérer l'application

1. Téléchargez l'application au format ZIP (bouton vert **Code →
   Download ZIP** sur la page GitHub du projet).
2. Faites un **clic droit** sur le fichier ZIP téléchargé → **Extraire tout…**
3. Ouvrez le dossier extrait, puis le sous-dossier **`installation`**.

## Étape 2 — Lancer l'installateur

1. Faites un **clic droit** sur le fichier **`Installer.ps1`**
   → **Exécuter avec PowerShell**.
2. Si Windows affiche un avertissement de sécurité, cliquez
   **Plus d'infos → Exécuter quand même**, puis **Oui** à la demande
   de droits administrateur.
3. Répondez aux questions (appuyez simplement sur **Entrée** pour
   accepter le choix proposé entre crochets) :

   | Question | Conseil |
   |---|---|
   | Dossier d'installation | Entrée (C:\AppGestion) |
   | Port de l'application | Entrée (8000) |
   | Accessible depuis d'autres postes ? | **o** si plusieurs ordinateurs l'utiliseront, sinon Entrée |
   | Port PostgreSQL | Entrée (5432) |
   | Mot de passe base de données | Entrée (généré automatiquement) |

4. L'installateur fait ensuite **tout le reste tout seul** : Python,
   la base de données PostgreSQL, l'application, le démarrage
   automatique avec Windows et la sauvegarde quotidienne (2h00).

> ⚠️ Si l'installateur affiche un mot de passe `postgres` à la fin,
> **notez-le** dans un endroit sûr.

## Étape 3 — Créer votre compte administrateur

À la fin, votre navigateur s'ouvre sur la **page d'installation de
l'application** : renseignez le nom de votre structure, votre email et
choisissez un mot de passe (8 caractères minimum). C'est ce compte qui
administre l'application.

C'est terminé ! L'application est accessible à l'adresse affichée par
l'installateur (par exemple `http://NOM-DU-PC:8000` depuis les autres
postes du réseau).

---

## Questions fréquentes

**L'application redémarre-t-elle avec l'ordinateur ?**
Oui : elle est installée comme « service Windows » et démarre toute
seule, sans session ouverte.

**Où sont mes données ?**
Dans la base PostgreSQL `appgestion` sur cet ordinateur, et les pièces
jointes dans `C:\AppGestion`. Une sauvegarde automatique tourne chaque
nuit à 2h00.

**Comment mettre à jour l'application ?**
Téléchargez le nouveau ZIP et relancez `Installer.ps1` : il réutilise
la base de données existante et remplace seulement le programme.

**Comment désinstaller ?**
Lancez `Desinstaller.ps1` (même dossier). Vos données sont conservées.

## Dépannage

| Problème | Solution |
|---|---|
| « winget n'est pas reconnu » | Windows trop ancien : installez « App Installer » depuis le Microsoft Store, ou installez manuellement [Python](https://www.python.org/downloads/) (cochez *Add to PATH*) et [PostgreSQL](https://www.postgresql.org/download/windows/), puis relancez. |
| « L'application n'a pas répondu à temps » | Ouvrez `C:\AppGestion\logs\service-err.log` et envoyez son contenu à votre support. |
| Page inaccessible depuis un autre poste | Vérifiez que vous avez répondu **o** à la question réseau ; sinon relancez `Installer.ps1`. |
| Mot de passe administrateur oublié | Depuis l'écran de connexion : « Mot de passe oublié » (nécessite la configuration email), sinon contactez votre support. |
