# P0.3F — Audit technique navigation/templates

Nouvelle page :

```text
/controle/navigation
```

Permission requise :

```text
controle:view
```

Contrôles effectués :

- endpoints manquants dans les `url_for(...)` des templates ;
- comparaisons `request.endpoint == ...` vers endpoint absent ;
- `has_perm(...)` résiduel dans les templates ;
- `func.strftime` dans le Python ;
- endpoints potentiellement manquants dans les `url_for(...)` Python.

L’audit est volontairement léger, mais il attrape les fantômes les plus fréquents.
