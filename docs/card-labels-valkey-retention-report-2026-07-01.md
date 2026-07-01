# Card: informe de retencion para labels-valkey

Status: ready
Owner: DevOps / Labels
Created: 2026-07-01
Cluster: k3s / namespace `skirmshop`
Datastore: `labels-valkey` via `labels-valkey-master.skirmshop.svc.cluster.local:6379`

## Objetivo

Sacar un informe read-only de retencion, edades, tamanos y referencias de las
keys de `labels-valkey`, para decidir que se conserva, que se archiva, que se
puede borrar y que politica de TTL/GC necesita labels/Synapse.

## Contexto verificado

Baseline runtime refrescado el 2026-07-01:

```text
db0: keys=57263, expires=56
used_memory: 125.32M
maxmemory: 0B
maxmemory_policy: noeviction
expired_keys: 8
evicted_keys: 0
rejected_connections: 0
```

Breakdown agregado por prefijo:

```text
document-*                         48476
poll-*.skirmshop payloads           4006
poll-*.skirmshop_metadata           4005
other *.skirmshop payload/metadata   272
synapse.io/v1/workflow-instances/*   422
synapse.io/v1/workflows/*              4
ups:*                                 56
text-documents-list                    1
tenant hash skirmshop                  1
index hashes                           2
other                                 18
```

Consumidores actuales de `labels-valkey`:

```text
labels-outbox-relay
labels-shopify-app
labels-synapse-api
labels-synapse-correlator
labels-synapse-janitor
labels-synapse-operator
labels-ups-adapter
```

Nota operativa: `k8s/synapse-janitor.yaml` conserva un comentario antiguo de
observe-only, pero los env actuales tienen `CLEANUP_ENABLED=true` y
`CLEANUP_DRY_RUN=false`. El informe debe reconciliar documentacion, comportamiento
runtime y metricas del janitor antes de proponer cambios.

## Fuera de alcance

- No borrar keys.
- No ejecutar `FLUSH*`, `DEL`, `UNLINK`, `EXPIRE`, `RESTORE`, `MIGRATE` ni
  cambios de config.
- No mover `labels-valkey` a `shared-valkey`.
- No reiniciar servicios salvo que se cree una card separada de remediacion.

## Trabajo pedido

- [ ] Inventario por familia de keys.
  - Evidencia: conteos por prefijo, tipo Redis, TTL, `MEMORY USAGE`/`STRLEN`
    agregado y top N por tamano.
- [ ] Informe de edades.
  - Evidencia: timestamps extraidos de payloads/metadata sin volcar contenido
    sensible; si una familia no contiene timestamp fiable, marcarla como
    `age_unknown` y explicar fuente alternativa.
- [ ] Informe de referencias.
  - Evidencia: para `document-*`, `poll-*` y `workflow-instances`, comprobar si
    hay indices, listas, hashes o filas Postgres que todavia los referencian.
- [ ] Informe del janitor actual.
  - Evidencia: env reales, logs recientes, metricas `synapse_cleanup_*`,
    `synapse_redis_*`, errores de archive y ultimo run.
- [ ] Clasificacion de accion por familia.
  - Evidencia: tabla con categorias `keep`, `ttl_candidate`,
    `archive_then_delete_candidate`, `delete_candidate`, `needs_code_fix`.
- [ ] Decision recomendada para `labels-valkey`.
  - Evidencia: propuesta concreta de retencion y riesgo: mantener aislado,
    reducir backlog, anadir TTL/GC, o abrir remediacion.

## Comandos read-only sugeridos

Keyspace y memoria:

```bash
kubectl -n skirmshop exec labels-valkey-0 -c valkey -- sh -lc '
  valkey-cli -h labels-valkey-master INFO keyspace
  valkey-cli -h labels-valkey-master INFO memory | egrep "^(used_memory_human|maxmemory_human|maxmemory_policy):"
  valkey-cli -h labels-valkey-master INFO stats | egrep "^(expired_keys|evicted_keys|rejected_connections):"
'
```

Topologia de consumidores:

```bash
kubectl -n skirmshop get deploy -o json | jq -r '
  .items[] as $d |
  [$d.metadata.name,
   (([$d.spec.template.spec.containers[]?.env[]?
      | select((.name|test("REDIS|VALKEY")) or ((.value? // "") | test("labels-valkey")))
      | (.value // "<secret/ref>") as $v
      | "\(.name)=\($v)"] | join("; ")))]
  | select(.[1] != "")
  | @tsv'
```

Tipos y tamanos por muestra segura:

```bash
kubectl -n skirmshop exec labels-valkey-0 -c valkey -- sh -lc '
  valkey-cli -h labels-valkey-master --scan |
  while read -r key; do
    type=$(valkey-cli -h labels-valkey-master TYPE "$key")
    ttl=$(valkey-cli -h labels-valkey-master TTL "$key")
    bytes=$(valkey-cli -h labels-valkey-master MEMORY USAGE "$key" 2>/dev/null || true)
    printf "%s\t%s\t%s\t%s\n" "$key" "$type" "$ttl" "${bytes:-unknown}"
  done
'
```

Janitor runtime:

```bash
kubectl -n skirmshop get deploy labels-synapse-janitor -o yaml
kubectl -n skirmshop logs deploy/labels-synapse-janitor --since=24h
kubectl -n skirmshop port-forward svc/labels-synapse-janitor 3000:3000
curl -fsS http://127.0.0.1:3000/metrics | egrep "synapse_cleanup_|synapse_redis_"
```

## Criterios de seguridad

- El informe no debe incluir contenido de documentos, OCR, direcciones,
  tracking numbers completos ni payloads completos.
- Los ejemplos deben estar hasheados o truncados.
- Cualquier propuesta de borrado debe incluir:
  - patron exacto,
  - conteo estimado,
  - bytes estimados,
  - edad minima,
  - prueba de no referencia,
  - rollback esperado desde snapshot/backup,
  - comando dry-run separado del comando mutante.

## Entregables

- `docs/labels-valkey-retention-report-YYYY-MM-DD.md`
- CSV opcional con inventario agregado, sin payload sensible.
- Recomendacion final: politica de retencion + si abrir una card de limpieza.

## Riesgos a resolver

- `document-*` puede contener OCR/texto sensible: no volcar valores.
- Redis no da edad nativa por key; la edad debe inferirse de payload/metadata,
  Postgres o logs.
- `noeviction` protege estado, pero tambien puede convertir acumulacion en
  incidente si no hay limite ni GC.
- El janitor ya parece activo; antes de tocarlo hay que entender que limpia hoy
  y que no cubre todavia.
