# P0.4F.1 — Restauration du hub Pédagogie

## Problème

Le layout pointe vers :

```text
pedagogie.index
```

Mais le patch P0.4F a supprimé par erreur la route :

```text
/pedagogie/
```

Résultat : `BuildError` dès que le layout est rendu, y compris sur `/dashboard`.

## Correction

La route `pedagogie.index` est restaurée dans :

```text
app/pedagogie/routes.py
```

## Migration

Aucune.
