# Imputer une séance à un autre secteur

Chaque séance porte un **secteur d'imputation** : c'est lui — et non le
secteur de l'atelier — qui décide dans quelles statistiques la séance et ses
présences sont comptées.

Concrètement : si vous animez une séance pour un autre secteur que le vôtre,
choisissez ce secteur à la création. La séance **sort de vos statistiques** et
**entre dans les siennes**, sans que vous perdiez la main dessus.

## Où le choisir

- **Création d'une séance** (Activité → atelier → nouvelle séance) : liste
  « Secteur d'imputation », pré-remplie avec le secteur de l'atelier.
- **Création en série** : le secteur choisi s'applique à toutes les séances
  générées ; chacune reste modifiable ensuite.
- **Après coup** : bouton de correction de date/heure de la séance
  (« edit-schedule »). Le changement est **tracé** au même titre qu'un
  changement de date : la raison saisie est préfixée de
  `[Imputation : ancien → nouveau]` dans le journal de la séance.

La liste déroulante n'apparaît que si plusieurs secteurs sont actifs, et un
libellé inconnu retombe toujours sur le secteur de l'atelier : une imputation
ne peut pas partir dans un secteur fantôme.

## Ce que ça change dans les statistiques

Tout le module Stats-Impact raisonne sur le secteur de la séance :
tableau de bord, volumes d'activité, fréquentation, démographie, occupation,
pédagogie, export fidèle et Magatomatique détaillé.

Dans l'export détaillé, un atelier apparaît chez un secteur dès qu'il porte au
moins une séance imputée à ce secteur, avec **les seules séances qui le
concernent**. Quand les séances d'une feuille ne viennent pas toutes du secteur
de l'atelier, une ligne « Séances imputées à : … » l'indique, pour qu'aucun
écart de total ne reste inexpliqué.

## Qui garde l'accès

Une séance reste accessible **aux deux secteurs concernés** : celui de
l'atelier (il l'a créée chez lui) et celui de l'imputation (ce sont ses
chiffres). Un atelier intersecteur reste accessible à tous, comme avant.
Dans la liste des séances et sur la page d'émargement, une pastille
« ↗ secteur » signale les séances imputées ailleurs.

## Points d'attention

- **Les séances existantes ne bougent pas** : elles portent déjà le secteur de
  leur atelier, donc les chiffres d'avant restent identiques.
- **Changer le secteur d'un atelier ne réécrit plus l'historique.** Auparavant
  les statistiques suivaient le secteur courant de l'atelier ; désormais chaque
  séance garde l'imputation qu'elle avait. Pour déplacer d'anciennes séances,
  passez par la correction séance par séance (tracée).
- La saisie en grille crée les séances dans le secteur de l'atelier : c'est une
  vue « mon secteur ». Corrigez ensuite si besoin.
