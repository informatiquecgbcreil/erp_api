# P0.4F.2 — Restauration du menu expert P0.3

## Problème

Après les patchs P0.4 pédagogie, le layout de navigation est revenu à une ancienne version.

Symptôme :

- ancien menu expert ;
- perte du comportement dropdown automatique ;
- menu moins lisible.

## Cause

Certains patchs P0.4 sont repartis de l’archive `AppGestion.zip`, plus ancienne que la base P0.3 validée.

## Correction

Ce patch restaure :

```text
app/templates/layout.html
```

depuis la source P0.3 validée :

```text
P03E_responsive_mobile_tablette.zip
```

et force le lien Pédagogie vers :

```text
pedagogie.index
```

donc :

```text
/pedagogie/
```

## Migration

Aucune.
