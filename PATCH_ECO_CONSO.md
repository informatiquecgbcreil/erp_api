# Patch ECO-CONSO — consommation individuelle par présence

## Objectif
Ajouter une consommation estimée individuelle à chaque présence, puis afficher le cumul par habitant sur la période de référence.

## Fonctionnement MVP
- La séance conserve sa consommation globale existante : matériel déclaré + durée + référentiel watts.
- Chaque présence peut désormais recevoir un matériel individuel utilisé.
- La consommation est figée dans une table dédiée au moment de l'émargement ou via application groupée.
- Le cumul affiché est calculé du 1er janvier de l'année de la séance jusqu'à la date de la séance incluse.
- Les feuilles DOCX/PDF générées reçoivent les champs : conso séance et cumul période.

## Migration ajoutée
- `34f5a6b7c8d9_presence_consumption_individual.py`

Commande habituelle :

```bash
flask db upgrade
```

## Fichiers principalement modifiés
- `app/models.py`
- `app/services/consumption.py`
- `app/activite/routes.py`
- `app/templates/activite/emargement.html`
- `app/activite/services/docx_utils.py`
- `app/activite/assets/modele_collectif.docx`
- `app/activite/assets/modele_individuel.docx`
- `app/activite/assets/modele_collectif_CSC.docx`
- `app/activite/assets/modele_individuel_CSC.docx`

## Notes
Les valeurs sont indicatives et destinées au suivi pédagogique/statistique. Elles ne doivent pas être présentées comme des mesures électriques certifiées.
