# labels-valkey retention report - 2026-07-01

## Scope

Read-only audit of `labels-valkey` in k3s namespace `skirmshop`.
No keys were deleted, expired, restored, migrated, or reconfigured. No services
were restarted.

Evidence was collected from:

- `kubectl -n skirmshop exec labels-valkey-0 -c valkey -- valkey-cli ...`
- `kubectl -n skirmshop get deploy/pods/logs`
- `kubectl -n skirmshop port-forward svc/labels-synapse-janitor ... /metrics`
- `kubectl -n databases exec postgres-shared-2 -- psql -U postgres -d labels ...`
- Repo files `k8s/synapse-janitor.yaml`, `k8s/monitoring.yaml`, and this repo's
  `docs/card-labels-valkey-retention-report-2026-07-01.md`

The companion aggregate CSV is
`docs/labels-valkey-inventory-2026-07-01.csv`. It contains only family-level
counts and no payload content.

## Executive Summary

`labels-valkey` should remain isolated for now. It stores Synapse runtime state,
workflow definitions, poll state, a large document backlog, and operational
indexes. Moving it to `shared-valkey` would mix critical state with cache
tenants before retention and reference ownership are clean.

Main findings:

- Keyspace is modest in memory but skewed: `57,303` keys and `125.34M`
  `used_memory`, with `document-*` consuming about `103.8M` by `MEMORY USAGE`
  aggregate.
- `maxmemory=0B` and `maxmemory_policy=noeviction`. This prevents accidental
  eviction of state, but accumulation can become an incident because there is no
  hard cap or universal GC.
- Only `ups:*` has Redis TTLs (`56` keys). All document, poll, workflow, index,
  and tenant keys are `TTL=-1`.
- `labels-synapse-janitor` is live, not observe-only:
  `CLEANUP_ENABLED=true`, `CLEANUP_DRY_RUN=false`.
- Janitor is healthy for workflow-instance cleanup: last observed pass archived
  and deleted `8`, `archiveFailed=0`, and metrics show zero orphan index entries.
- `document-*` is the main retention risk: `48,510` strings, no TTL, no reliable
  Redis age, no Postgres document table, and no checked Redis/Postgres references
  found. This is not enough to delete; it is enough to open a remediation card.

Recommended decision: keep `labels-valkey` isolated, keep the janitor active for
Synapse workflow instances, and open a cleanup/remediation card for `document-*`
and `poll-*` retention. Do not delete `document-*` or `poll-*` in this card.

## Runtime Baseline

Captured on 2026-07-01 around 15:58-16:00 UTC.

```text
db0:keys=57303,expires=56,avg_ttl=1148670531
used_memory_human:125.34M
maxmemory_human:0B
maxmemory_policy:noeviction
rejected_connections:0
expired_keys:8
evicted_keys:0
```

Pods checked:

- `labels-valkey-0/1/2`: `2/2 Running`, age `20d`
- `labels-valkey-master-labeler`: `1/1 Running`
- `labels-synapse-janitor`: `1/1 Running`, age `3d`
- `labels-shopify-app`, `labels-outbox-relay`, `labels-ups-adapter`,
  `labels-synapse-api`, `labels-synapse-correlator`,
  `labels-synapse-operator`: running

Current consumers of `labels-valkey-master.skirmshop.svc.cluster.local:6379`:

- `labels-outbox-relay`
- `labels-shopify-app`
- `labels-synapse-api`
- `labels-synapse-correlator`
- `labels-synapse-janitor`
- `labels-synapse-operator`
- `labels-ups-adapter`

## Inventory By Family

`MEMORY USAGE` is per-key Redis memory and will not exactly equal
`INFO memory used_memory`. The family inventory is a live `SCAN` aggregate and
summed to `57,298` keys. `INFO keyspace` reported `57,303` immediately before
the scan and `57,304` on a later recheck, so the 5-6 key delta is runtime drift
from active workers/janitor rather than an accounting claim.

| Family | Count | Type(s) | Bytes | Avg bytes | STRLEN sum | TTL |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| `document-*` | 48,510 | string | 103,825,696 | 2,140 | 95,753,717 | none |
| `poll-*.skirmshop` | 4,008 | string | 9,385,144 | 2,341 | 8,130,575 | none |
| `poll-*.skirmshop_metadata` | 4,007 | string | 1,132,304 | 282 | 696,613 | none |
| other `*.skirmshop*` | 272 | string | 494,336 | 1,817 | 416,076 | none |
| `synapse.io/v1/workflow-instances/*` | 419 | string | 1,542,688 | 3,681 | 1,377,954 | none |
| `synapse.io/v1/workflows/*` | 4 | string | 182,144 | 45,536 | 176,029 | none |
| `ups:*` | 56 | string | 10,752 | 192 | 5,554 | 56 expiring |
| `text-documents-list` | 1 | list | 230,576 | 230,576 | n/a | none |
| tenant hash `skirmshop` | 1 | hash | 1,268,376 | 1,268,376 | n/a | none |
| index hashes | 2 | hash | 89,672 | 44,836 | n/a | none |
| other | 18 | string/hash | 10,334 | 574 | 6,221 | none |

Top-size examples are pseudonymized with a 16-char SHA1 prefix of the key name.

| Family | Rank | Bytes | STRLEN | Key hash |
| --- | ---: | ---: | ---: | --- |
| `document-*` | 1 | 163,888 | 156,765 | `a7b2e26ca6b452fc` |
| `document-*` | 2 | 163,888 | 156,200 | `657b49d56c996112` |
| `document-*` | 3 | 163,888 | 162,357 | `c559205a1c7f300c` |
| `poll-*.skirmshop` | 1 | 10,320 | 9,091 | `c301282896f0d6c3` |
| `poll-*.skirmshop` | 2 | 10,320 | 8,281 | `0ac83c3853c4edc5` |
| `poll-*.skirmshop` | 3 | 10,320 | 9,051 | `6ea2c53e1ec235ec` |
| `workflow-instances` | 1 | 16,512 | 15,355 | `aae9443b5daed194` |
| `workflow-instances` | 2 | 14,464 | 12,635 | `cd8c0b68ff3832ca` |
| `workflow-instances` | 3 | 14,464 | 12,946 | `cfdfbb45b68080e3` |
| `workflows` | 1 | 131,168 | 127,343 | `11c61f59e1740066` |
| tenant hash `skirmshop` | 1 | 1,268,376 | n/a | `d65bf315f095a827` |
| `text-documents-list` | 1 | 230,576 | n/a | `793731d7d1be9e61` |

## Age Report

Redis does not expose native per-key creation time. Ages below are inferred only
from explicit timestamp fields found in payload/metadata. OCR/text content was
not dumped and was not used as an age source.

| Family | Keys with timestamp | Source field(s) | Min | Max | Notes |
| --- | ---: | --- | --- | --- | --- |
| `poll-*.skirmshop_metadata` | 4,007 / 4,007 | `createdAt` | 2026-05-31T17:45:10Z | 2026-07-01T15:45:05Z | Reliable metadata age. |
| `poll-*.skirmshop` | 0 / 4,008 | none found | n/a | n/a | Payload age should inherit paired metadata when present. |
| other `*.skirmshop*` | 136 / 272 | `createdAt` | 2026-06-01T10:13:22Z | 2026-07-01T12:16:53Z | Half lack known timestamp fields. |
| `workflow-instances` | 419 / 419 | `startedAt`, `createdAt` | 2026-06-15T09:49:11Z | 2026-07-01T16:00:09Z | Janitor-managed. |
| `workflows` | 0 / 4 | none found | n/a | n/a | Workflow definitions; keep. |
| `document-*` | 0 reliable | age_unknown | n/a | n/a | Redis has no key age; document strings may contain OCR/text dates, which are not reliable metadata. No Postgres document table or document refs were found. |

## Reference Report

### Redis References

Workflow indexes:

```text
workflow-instances.synapse.io HLEN=419
workflow-instances index values pointing at live workflow keys=419
workflow instance keys total=419
workflows.synapse.io HLEN=4
workflow definition keys total=4
```

The index fields are not the key names, but the hash values are exact live
`synapse.io/v1/workflow-instances/*` keys. Metrics also report
`synapse_redis_orphan_index_entries_total=0`.

Poll pairs:

```text
poll payload keys=4008
payload keys with metadata pair=4007
metadata keys=4007
metadata keys with payload pair=4007
payload id has matching workflow-instance key=0
```

`text-documents-list`:

```text
LLEN=4143
members pointing at live keys=4143
member families: poll_payload=4007, other_skirmshop_payload_metadata=136
members pointing at document-* keys=0
```

Despite its name, the list currently indexes poll/other `.skirmshop` payloads,
not `document-*`.

Tenant and index hashes:

```text
skirmshop HLEN=6709
skirmshop hash values pointing at live keys=425
  workflow_instances=419
  workflows=4
  other=2
skirmshop values containing "document-"=0
workflow-instances.synapse.io values pointing at live workflow keys=419
workflows.synapse.io values pointing at live workflow keys=4
```

No Redis list/hash/index checked here references `document-*`.

### Postgres References

Schema inspection found no document table. Text search counts for `document-`
inside event JSON were zero:

```text
event_store payload refs containing document-: 0
failed_events refs containing document-: 0
```

`tracking_poll_attempt` exists, but it is much smaller than Redis poll keys and
does not directly match Redis poll IDs by hash:

```text
tracking_poll_attempt rows=423
first_attempt_at min=2026-05-30 13:15:00+00
last_attempt_at max=2026-07-01 16:00:03+00
Redis poll payload unique ids=4008
Redis poll metadata unique ids=4007
Intersection with tracking_poll_attempt.shipment_id=0
```

`workflow_instance` archives exist in Postgres, but current Redis live workflow
keys do not match archived row IDs or raw reference suffixes by hash:

```text
workflow_instance rows=8966
started_at min=2026-05-29 13:21:28+00
started_at max=2026-06-30 15:45:07+00
archived_at max=2026-07-01 15:52:36+00
statuses: completed=4078, faulted=4884, running=3, unknown=1
Redis workflow live unique ids=419
Intersection with workflow_instance_id=0
Intersection with raw_synapse_ref last segment=0
```

This is consistent with janitor behavior: terminal instances are archived before
deletion; live Redis instances are the retained/non-candidate set, not rows that
have already been deleted from Redis.

## Janitor Current State

Repo and runtime agree on live cleanup env, while the header comment in
`k8s/synapse-janitor.yaml` is stale.

Runtime env:

```text
CLEANUP_ENABLED=true
CLEANUP_DRY_RUN=false
CLEANUP_MIN_AGE_HOURS=6
CLEANUP_RETENTION_COMPLETED_HOURS=24
CLEANUP_RETENTION_FAULTED_HOURS=48
CLEANUP_RETENTION_PENDING_POLL_HOURS=24
CLEANUP_INTERVAL_SECONDS=900
ORPHAN_GRACE_SECONDS=120
MANUAL_LABEL_STUCK_MINUTES=15
SYNAPSE_INDEX_KEY=workflow-instances.synapse.io
```

Recent log evidence:

```json
{"scanned":426,"parseFailed":0,"manualLabelStuck":0,"orphanCandidates":0,"orphansConfirmed":0,"orphansDeleted":0,"deleteCandidates":8,"deleted":8,"archived":8,"archiveFailed":0,"changed":0,"enabled":true,"dryRun":false,"msg":"janitor.pass-complete"}
```

Metrics evidence:

```text
synapse_redis_workflow_instances_total{workflow="poll-tracking",phase="completed"} 401
synapse_redis_workflow_instances_total{workflow="poll-tracking",phase="faulted"} 3
synapse_redis_workflow_instances_total{workflow="generate-shipment-label",phase="completed"} 9
synapse_redis_workflow_instances_total{workflow="generate-shipment-label",phase="faulted"} 13
synapse_redis_orphan_index_entries_total 0
synapse_cleanup_deleted_total poll-tracking/completed=480
synapse_cleanup_deleted_total poll-tracking/faulted=179
synapse_cleanup_deleted_total generate-shipment-label/completed=20
synapse_cleanup_archived_total 679
synapse_cleanup_archive_failed_total 0
synapse_cleanup_runs_total{outcome="ok"} 289
synapse_cleanup_last_run_timestamp_seconds 1782921151
last run human time: 2026-07-01 15:52:31 UTC / 17:52:31 CEST
```

Conclusion: current janitor is doing useful live cleanup for Synapse workflow
instances. It does not cover `document-*` or `poll-*` payload retention.

## Action Classification

| Family | Classification | Evidence | Recommendation |
| --- | --- | --- | --- |
| `document-*` | `archive_then_delete_candidate`, `needs_code_fix` | 48,510 keys, 103.8M bytes, no TTL, no reliable age, no checked Redis/Postgres refs. | Open remediation card. First add/export metadata or archive path, take snapshot, then dry-run by pattern and age. No delete now. |
| `poll-*.skirmshop` | `ttl_candidate`, `needs_code_fix` | 4,008 payloads, 4,007 metadata pairs, no payload timestamp, no Redis TTL, no direct PG shipment refs. | Add retention based on metadata `createdAt` and/or final tracking status. Investigate one unpaired payload. |
| `poll-*.skirmshop_metadata` | `ttl_candidate` | 4,007 metadata rows with `createdAt`, oldest 2026-05-31, no TTL. | TTL with paired payload, after defining active tracking retention. |
| other `*.skirmshop*` | `ttl_candidate`, `needs_code_fix` | 272 keys; 136 have `createdAt`, 136 age_unknown. | Identify producer and apply same poll retention or move to explicit family. |
| `workflow_instances` | `keep`, janitor-managed | 419 live keys, all indexed, timestamps present, janitor archiving/deleting terminal backlog. | Keep janitor policy; monitor orphan/archive failure alerts. |
| `workflows` | `keep` | 4 definitions, indexed. | Keep without TTL. |
| `ups:*` | `keep` | 56 keys, all have TTL. | Keep current TTL behavior. |
| `text-documents-list` | `needs_code_fix` | Name says documents, members point to poll/other `.skirmshop` payloads. | Rename/rebuild or document actual purpose; do not use as proof for `document-*`. |
| tenant/index hashes | `keep` | Values point at live workflow/workflow definition keys; no document refs. | Keep; include in janitor/reference checks. |
| other | `needs_code_fix` | 18 keys, mixed string/hash, no TTL. | Inventory producer before deletion. |

## Retention Proposal

1. Keep `labels-valkey` isolated from `shared-valkey`.
2. Keep `maxmemory_policy=noeviction` until all critical state has explicit
   archive/restore semantics. Add a separate capacity alert/remediation if
   `used_memory` keeps growing.
3. Keep `labels-synapse-janitor` enabled for workflow instances. The current
   policy is working and has `archiveFailed=0`.
4. Fix the stale observe-only comment in `k8s/synapse-janitor.yaml` in a small
   follow-up docs/config hygiene card.
5. Open remediation for `document-*`:
   - exact pattern: `document-*`
   - current estimate: `48,510` keys, `103,825,696` bytes by `MEMORY USAGE`
   - age: `age_unknown`
   - references checked: `text-documents-list`, tenant hash, Synapse index
     hashes, `event_store`, `failed_events`
   - blocker: no reliable age or archive proof
   - rollback expectation: restore from Valkey snapshot/RDB/PVC backup taken
     immediately before any mutation
   - dry-run first: scan/count/hash/memory only; no `UNLINK`/`DEL` command is
     approved by this report
6. Open remediation for `poll-*`:
   - exact patterns: `poll-*.skirmshop`, `poll-*.skirmshop_metadata`
   - current estimate: `8,015` keys, `10,517,448` bytes
   - age: metadata `createdAt` spans 2026-05-31 to 2026-07-01
   - references checked: payload/metadata pairing, `text-documents-list`,
     `tracking_poll_attempt`
   - blocker: Redis poll IDs do not match `tracking_poll_attempt.shipment_id`;
     owner/producer must define the ID contract before deletion
   - proposed policy after contract is known: expire completed/terminal tracking
     poll payload+metadata after 30-45 days; retain active/null-status rows until
     tracking workflow closes them.

## Verification Checklist

- [x] Runtime keyspace, memory, stats captured read-only.
- [x] Consumers of `labels-valkey` identified from live Deployments.
- [x] Family inventory captured with type, TTL, `MEMORY USAGE`, `STRLEN`, and
  pseudonymized top-size keys.
- [x] Timestamp fields extracted from poll metadata, workflow instances, and
  scoped `.skirmshop` payload families without printing payloads.
- [x] Redis references checked for workflow indexes, workflows index,
  `text-documents-list`, tenant hash, and hash values.
- [x] Postgres schema and aggregate references checked read-only.
- [x] Janitor env, recent log, and metrics reconciled.
- [x] No destructive Redis/Kubernetes/Postgres command executed.

## Residual Risks

- `document-*` may contain OCR/text and should be treated as sensitive. Do not
  inspect or export values outside a redacted remediation script.
- Redis cannot answer per-key age. Any `document-*` retention policy needs an
  external timestamp source or archive manifest before deletion.
- `text-documents-list` is misleadingly named or has shifted purpose; it
  currently references poll/other `.skirmshop` payloads, not `document-*`.
- `poll-*` Redis IDs do not match `tracking_poll_attempt.shipment_id`; deleting
  by age alone could break an unobserved runtime contract.
- `noeviction` is correct for safety but still needs memory growth alerting and
  capacity planning.
