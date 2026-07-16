# Intégration « Portail des apprenants » — contrat client

Document de passation décrivant ce que l'application **Gestion du Centre** (client)
envoie, attend et stocke vis-à-vis du **Portail des apprenants**
(`https://www.comprendre.cgbcreil.com`). Sert de référence partagée pour vérifier
que l'API du portail correspond à ce que le client implémente.

## 1. But

- **À l'inscription d'un stagiaire** : le client crée son *code apprenant* sur le
  portail et le stocke sur la fiche participant.
- **Périodiquement (tâche planifiée)** : le client récupère les tentatives
  (scores/durées) et les range dans ses stats.
- Le portail **ne stocke aucune donnée personnelle** : on ne lui transmet que
  notre **ID interne** (`externalId = Participant.id`, entier stable).

## 2. Configuration (côté client, dans `.env`, jamais en dur)

```
PORTAIL_BASE_URL=https://www.comprendre.cgbcreil.com
PORTAIL_TOKEN=<jeton secret>
```

Tant que ces deux valeurs ne sont pas renseignées, l'intégration est **désactivée**
(aucun appel réseau).

## 3. Authentification

Chaque requête envoie l'en-tête :

```
x-api-token: <PORTAIL_TOKEN>
```

Plus `Accept: application/json`, et `Content-Type: application/json` sur le POST.

## 4. Endpoints appelés

### a) Santé — `GET {BASE}/api/health`

Réponse attendue : `{"ok": true}`. Utilisé par `tools/portail_health.py`.

### b) Création/idempotence du code — `POST {BASE}/api/integration/learners`

- Corps : `{"externalId": "<Participant.id en chaîne>"}`
- Réponse attendue : `{"code": "<string>", "created": <bool>}`
- **Idempotent** : même `externalId` ⇒ **même `code`**.
- Appelé à l'inscription (création d'un parcours insertion).

### c) Récupération des résultats — `GET {BASE}/api/integration/attempts?since=<ISO8601>`

`since` est omis au premier appel, puis vaut le `generatedAt` du passage précédent.

```json
{
  "generatedAt": "2026-06-16T11:00:00Z",
  "attempts": [
    {
      "id": "att-123",
      "externalId": "42",
      "activity": "Quiz A",
      "activityType": "quiz",
      "theme": "Maths",
      "score": 8,
      "maxScore": 10,
      "pct": 80,
      "durationMs": 120000,
      "finishedAt": "2026-06-16T09:30:00Z"
    }
  ]
}
```

Noms de champs en **camelCase exactement** comme ci-dessus (`externalId`,
`activityType`, `maxScore`, `durationMs`, `finishedAt`, `generatedAt`).

## 5. Sémantique attendue (ce que le portail doit garantir)

- **`attempts[].id`** : identifiant **unique et stable** de la tentative. C'est la
  **clé de déduplication** côté client. La borne `since` étant traitée comme
  **inclusive**, une même tentative peut revenir : le client fait un *update*, pas
  un doublon.
- **`since` inclusif accepté** (le client gère les chevauchements par dédup sur `id`).
- **`generatedAt`** renvoyé et **réutilisable tel quel comme prochain `since`**.
- **`externalId`** renvoyé **à l'identique** de ce qu'on a transmis (notre
  `Participant.id` en chaîne), ou **`null`**. Si `null` ⇒ ligne **ignorée**. Si
  non-null mais inconnu chez nous ⇒ trace **conservée** sans rattachement.
- `finishedAt` / `generatedAt` au format **ISO 8601** (le `Z` final est géré).

## 6. Gestion d'erreurs (côté client)

- Erreur réseau / HTTP / réponse non-JSON ⇒ exception interne `PortailError`.
- **Création du code à l'inscription** : erreur **avalée et journalisée**,
  l'inscription n'échoue jamais (le code sera créé plus tard).
- **Synchro planifiée** : en cas d'échec, sortie en code 1 **sans avancer le
  curseur `since`** ⇒ réessai au passage suivant, sans perte ni plantage.

## 7. Stockage côté client

- `participant.portail_code` — le code apprenant (1 par participant).
- Table **`portail_attempt`** : `attempt_id` (UNIQUE = l'`id` du portail),
  `external_id`, `participant_id` (FK, ON DELETE SET NULL), `activity`,
  `activity_type`, `theme`, `score`, `max_score`, `pct`, `duration_ms`,
  `finished_at`, `created_at`.
- Table **`portail_sync_state`** (ligne unique id=1) : `last_since`, `last_run_at`,
  `last_status`.

## 8. Déclenchement

- **Code à l'inscription** : `app/insertion/routes.py`, après création d'un parcours
  insertion (`is_new`), appel non bloquant à `assurer_code_portail(participant)`.
- **Synchro** : `tools/sync_portail.py` (+ `sync_portail_now.bat`), à planifier
  (Tâche planifiée Windows / cron), comme la sauvegarde et la publication.

## 9. Détails techniques

- Client HTTP en **`urllib` (bibliothèque standard)** — aucune dépendance ajoutée.
- Migration Alembic **manuelle défensive** `7d8e9f0a1b23`
  (down_revision `6c7d8e9f0a12`) : ajoute `participant.portail_code` + crée
  `portail_attempt` et `portail_sync_state`.

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `config.py` | `PORTAIL_BASE_URL`, `PORTAIL_TOKEN` |
| `app/models.py` | `Participant.portail_code`, `PortailAttempt`, `PortailSyncState` |
| `app/services/portail_apprenants.py` | client + logique |
| `app/insertion/routes.py` | accroche à l'inscription |
| `migrations/versions/7d8e9f0a1b23_portail_apprenants.py` | migration |
| `tools/sync_portail.py`, `sync_portail_now.bat` | tâche planifiée |
| `tools/portail_health.py` | test de connexion |
| `tests/test_portail_apprenants.py` | tests |

## 11. Points à confirmer côté portail

1. `POST /api/integration/learners` **idempotent** par `externalId`.
2. `attempts[].id` **unique et stable**.
3. `externalId` **renvoyé à l'identique** (ou null).
4. Filtre `since` **inclusif** + `generatedAt` réutilisable comme prochain `since`.
5. En-tête d'auth bien `x-api-token`.
6. `GET /api/health` → `{"ok": true}`.
