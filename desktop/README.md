# Construire la distribution Windows

Depuis Windows x64 et Python 3.13 x64, installer les dépendances de construction
dans un environnement isolé (Pillow 12.3.0 pour l'icône) puis exécuter :

```text
python desktop/build.py --work C:/build/mcs-01 --output C:/livraison --iscc "C:/Inno Setup 6/ISCC.exe"
```

Le compilateur C# est fourni par .NET Framework dans Windows. Inno Setup 6.7.3
est l'outil de construction utilisé. Les installations finales n'ont besoin
d'aucun de ces outils de développement.

`build.py` refuse d'écraser un dossier payload existant, vérifie les empreintes
des composants téléchargés, installe toutes les bibliothèques depuis
`requirements-windows.lock` avec validation des hashes, compile le gestionnaire
Windows et produit l'EXE, son SHA-256 et un manifeste des fichiers. L'archive
des sources correspondantes est incluse pour respecter l'AGPL.

Les archives peuvent être prépositionnées avec `--downloads C:/cache/mcs`.
Le lien Microsoft Visual C++ est mutable : si son empreinte change, la
construction refuse la nouvelle archive. Vérifier alors sa signature Microsoft
et mettre explicitement à jour l'empreinte, sans désactiver la vérification.

Le runtime embarqué CPython est isolé via `python313._pth`. Le compte virtuel du
service a accès aux données, mais pas au dossier de direction. Le gestionnaire
décrypte DPAPI et passe les secrets par stdin UTF-8. PostgreSQL est limité à la
boucle locale et utilise SCRAM ; l'application utilise un compte non privilégié.
Le service contient les processus dans un Job Object Windows pour éviter les
processus orphelins après un arrêt forcé.

Les dépendances JavaScript sont hébergées dans `app/static/vendor`. Le fichier
`vendor-package-lock.json` renseigne les versions et intégrités npm de Bootstrap
5.3.8 et Chart.js 4.5.1 ; aucun CDN n'est requis pour ces fonctionnalités.

## Validation

```text
python -m pytest
python -m pip_audit --require-hashes -r desktop/requirements-windows.lock
MonCentreSocial.exe --self-test
```

La recette du runtime doit aussi couvrir une base vierge, HTTPS avec vérification
du certificat, connexion CSRF, choix des modules, sauvegarde, arrêt et redémarrage
avec conservation des données, et un chemin comportant des espaces/accents.
Une recette administrateur sur machines Windows/Windows Server propres est
nécessaire pour valider UAC, service SCM, ACL, pare-feu, réparation, mise à jour
et désinstallation. La compilation et les tests applicatifs ne remplacent pas
cette recette système.

Pour la diffusion officielle, signer le gestionnaire et l'installateur avec le
certificat Authenticode de l'éditeur, puis recalculer les empreintes. Aucun
certificat ni mot de passe de signature ne doit entrer dans le dépôt.
