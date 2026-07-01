# labels-valkey remediation plan — 2026-07-01

Follow-up to `docs/labels-valkey-retention-report-2026-07-01.md`. That report is a
read-only audit. This document is the **remediation plan**: two runbook cards
(`document-*` remediation and `poll-*` TTL/GC) plus the observability follow-up.

## Ground rules for this plan

- **Nothing in this plan is approved for execution today.** Every mutating command
  is fenced under an explicit `NO EJECUTAR` block and is separated from the
  read-only dry-run commands.
- No key is deleted, expired, restored, migrated, or reconfigured, and no service
  is restarted, by reading this document.
- No payload content, OCR text, addresses, or full tracking numbers appear here.
  Key examples reuse the 16-char SHA1 prefixes already published in the report.
- Source of truth for counts/bytes: the read-only report and
  `docs/labels-valkey-inventory-2026-07-01.csv`, captured 2026-07-01 ~15:58-16:00
  UTC. Re-capture before acting; the keyspace drifts with live workers.
- Datastore: `labels-valkey`, reached via
  `labels-valkey-master.skirmshop.svc.cluster.local:6379`. `maxmemory=0`,
  `maxmemory_policy=noeviction` — there is **no automatic eviction and no hard
  cap**, so accumulation is an availability risk, not just a cost one.

## Shared prerequisites (both cards)

### Snapshot / backup contract

Before **any** mutation on `labels-valkey` you must have a restorable point-in-time
copy. The StatefulSet persists to a Longhorn PVC (`data`, 8Gi, storageClass
`longhorn`) with `appendonly yes` and `--save ''` (AOF only, no RDB snapshots by
schedule). Two independent restore paths:

1. **Valkey-native AOF/RDB snapshot** on the current master pod (`BGSAVE` writes
   a local `dump.rdb`, but does not mutate the keyspace):

   ```bash
   # SNAPSHOT PREP: force an on-disk RDB snapshot next to the AOF, then copy it out.
   MASTER=$(kubectl -n skirmshop get pod -l app=labels-valkey,valkey-role=master \
     -o jsonpath='{.items[0].metadata.name}')
   kubectl -n skirmshop exec "$MASTER" -c valkey -- valkey-cli -p 6379 BGSAVE
   kubectl -n skirmshop exec "$MASTER" -c valkey -- valkey-cli -p 6379 INFO persistence \
     | egrep '^(rdb_bgsave_in_progress|rdb_last_bgsave_status|aof_last_write_status):'
   # After rdb_bgsave_in_progress:0 and rdb_last_bgsave_status:ok, copy the dump out:
   kubectl -n skirmshop cp "$MASTER":/data/dump.rdb ./labels-valkey-dump-$(date -u +%Y%m%dT%H%M%SZ).rdb -c valkey
   ```

2. **Longhorn PVC snapshot/backup** of the `data` PVC bound to the master pod, via
   the Longhorn UI or a `VolumeSnapshot`. This is the durable rollback of record
   because it captures the whole `/data` (AOF + RDB) atomically at the CSI layer.

Blocker: do not proceed to any dry-run-then-mutate step until **both** the RDB
copy exists off-pod **and** a Longhorn snapshot/backup of `data` is confirmed.

### Rollback expectation (both cards)

- Primary rollback: restore the Longhorn `data` PVC snapshot taken immediately
  before the mutation, then let Sentinel re-elect / resync replicas.
- Secondary rollback: `RESTORE` individual keys from the RDB copy for a scoped
  revert. `RESTORE` is a **mutating** command and lives in the `NO EJECUTAR`
  blocks below.
- There is **no** application-level re-hydration for `document-*` or `poll-*`
  payloads proven in this repo, so the snapshot is the only rollback. If the
  snapshot is missing or unverified → **hard stop, do not delete**.

### Universal blocking criteria (either card must abort if any is true)

- [ ] No verified snapshot (RDB copy + Longhorn snapshot of `data`) exists.
- [ ] The read-only re-capture disagrees materially with the 2026-07-01 baseline
      (family counts off by more than runtime drift) — re-audit first.
- [ ] A reference check finds **any** live index/list/hash/Postgres row pointing at
      a candidate key.
- [ ] `maxmemory_policy` is no longer `noeviction`, or `maxmemory` is non-zero
      (someone changed capacity policy out-of-band).
- [ ] Sentinel/failover is mid-event, a replica is desynced, or the master pod is
      not fully Ready — never mutate during a topology change (single-writer).
- [ ] The owning producer has not signed off the ID/retention contract (poll card).

---

## Card A — `document-*` remediation

Status: **blocked / planning only**
Owner: DevOps / Labels
Classification (from report): `archive_then_delete_candidate`, `needs_code_fix`

### What and why

- Exact pattern: `document-*`
- Estimate (2026-07-01 baseline): `48,510` keys, `103,825,696` bytes by
  `MEMORY USAGE` (~103.8M, the dominant share of `used_memory` 125.34M),
  `STRLEN` sum `95,753,717`.
- Redis type: `string`. TTL: none (`-1`) on every key.
- Age: `age_unknown`. Redis exposes no per-key creation time and there is **no
  reliable timestamp field** for `document-*` (OCR/text dates inside the value are
  not trustworthy metadata and must not be dumped).
- References checked and found **zero**: `text-documents-list` (see §"About
  text-documents-list"), tenant hash `skirmshop`, Synapse index hashes,
  `event_store`, `failed_events`. No Postgres document table exists.

### Root-cause posture (no workaround)

Deleting `48k` strings by pattern with no age source and no archive path is a
workaround, not a fix. The correct sequence is **code-fix first**:

1. Add a durable timestamp/metadata (or an archive manifest in Postgres) for
   `document-*` at write time, OR confirm the producer already writes an
   external record we can join against.
2. Only once every candidate has a provable age **and** a proven archive can we
   consider expiry/deletion. Until then this card stays blocked at the dry-run
   gate.

### Read-only dry-run (safe to run after snapshot)

These commands only `SCAN`, count, size, and hash. They never write.

```bash
# READ-ONLY: recount the family and its memory, no key content printed.
MASTER=$(kubectl -n skirmshop get pod -l app=labels-valkey,valkey-role=master \
  -o jsonpath='{.items[0].metadata.name}')
kubectl -n skirmshop exec "$MASTER" -c valkey -- sh -lc '
  tmp=$(mktemp)
  valkey-cli -p 6379 --scan --pattern "document-*" > "$tmp"
  n=$(wc -l < "$tmp")
  bytes=0
  while IFS= read -r k; do
    b=$(valkey-cli -p 6379 MEMORY USAGE "$k" 2>/dev/null || echo 0)
    bytes=$((bytes+b))
  done < "$tmp"
  rm -f "$tmp"
  printf "document_keys=%s memory_usage_bytes=%s\n" "$n" "$bytes"
'
```

### Non-reference proof (must be re-run and stay zero)

```bash
# READ-ONLY: prove no live structure references document-* before any delete.
MASTER=$(kubectl -n skirmshop get pod -l app=labels-valkey,valkey-role=master \
  -o jsonpath='{.items[0].metadata.name}')
kubectl -n skirmshop exec "$MASTER" -c valkey -- sh -lc '
  echo "text-documents-list members pointing at document-*:"
  valkey-cli -p 6379 LRANGE text-documents-list 0 -1 | grep -c "^document-" || true
  echo "tenant hash skirmshop values containing document-:"
  valkey-cli -p 6379 HVALS skirmshop | grep -c "document-" || true
  echo "workflow index values containing document-:"
  valkey-cli -p 6379 HVALS workflow-instances.synapse.io | grep -c "document-" || true
'
# READ-ONLY (Postgres): document- refs inside event JSON must stay 0.
# kubectl -n databases exec postgres-shared-2 -- psql -U postgres -d labels -c \
#   "select count(*) from event_store where payload::text like '%document-%';"
```

Acceptance to leave the dry-run gate: every counter above is `0` on a fresh
capture **and** each candidate key has a provable age from the new metadata/
archive introduced by the code-fix. If any counter is non-zero → **abort**.

### Mutating commands — NO EJECUTAR (planning reference only)

> The block below is **NOT approved**. It exists only to document exactly what a
> future, signed-off remediation would run, and to keep it visibly separate from
> the read-only commands above. Do **not** paste these into a live shell.

```bash
### NO EJECUTAR — document-* destructive remediation (requires: snapshot verified,
### age source shipped, zero-reference proof green, change ticket + owner sign-off)
###
### # 1. Optional archive to Postgres/object store BEFORE delete (must exist first).
### # 2. Delete by pattern + age, in bounded batches, UNLINK (non-blocking) not DEL:
### MASTER=... # current master pod
### kubectl -n skirmshop exec "$MASTER" -c valkey -- sh -lc '
###   valkey-cli -p 6379 --scan --pattern "document-*" \
###     | head -n 1000 \
###     | xargs -r -n 100 valkey-cli -p 6379 UNLINK
### '
### # Rollback: restore Longhorn snapshot of PVC data, or RESTORE keys from RDB copy.
```

---

## Card B — `poll-*` TTL / GC

Status: **forward cleanup implemented / historical bulk cleanup blocked**
Owner: DevOps / Labels
Classification (from report): `ttl_candidate`, `needs_code_fix`

### What and why

- Exact patterns: `poll-*.skirmshop` (payload) and `poll-*.skirmshop_metadata`
  (metadata).
- Estimate (2026-07-01 baseline): payloads `4,008` keys / `9,385,144` bytes;
  metadata `4,007` keys / `1,132,304` bytes. Combined `8,015` keys /
  `10,517,448` bytes.
- Redis type: `string`. TTL: none (`-1`) on both families.
- Age: metadata carries a **reliable** `createdAt` (4,007/4,007) spanning
  `2026-05-31T17:45:10Z` → `2026-07-01T15:45:05Z`. Payloads carry **no** timestamp
  of their own (0/4,008) and must inherit the paired metadata age.
- Pairing: `4,007` payload↔metadata pairs; **one unpaired payload** to investigate
  before any bulk action.

### Contract proof and current blocker

Tanda 2 found the producer contract in the tracking-poll source:

- Poll workflow instances are named
  `poll-${shipmentNameSegment(shipmentId)}-${bucket}`.
- `shipmentNameSegment()` keeps short UUID-like shipment IDs as-is, and maps
  external or long shipment IDs to `h${sha256(shipmentId).slice(0,12)}`.
- The workflow input still carries the raw `shipmentId`; the live
  `poll-tracking` v1.3.4 ConfigMap accepts external IDs (`maxLength: 120`), not
  UUID-only shipment IDs.

Read-only reconciliation against live Redis/Postgres confirmed the mapping shape
without printing payloads or tracking numbers:

```text
redis_unique_segments=327
pg_unique_segments=423
intersection=317
redis_segments_h=124 redis_segments_uuid=193 redis_segments_other=10
pg_segments_h=222 pg_segments_uuid=201 pg_segments_other=0
```

So the original `intersection=0` finding was an artifact of comparing the raw
Postgres shipment ID to the shortened/hash workflow segment. Historical bulk
cleanup is still **blocked** until a restorable snapshot exists, the 10 legacy
`redis_segments_other` names and the single unpaired payload are explained, and a
bounded batch plan is approved.

### Implemented forward policy

Source commit `97df077` in `skirmshop-labels` adds the safe forward cleanup to
`labels-synapse-janitor`. When an already eligible `poll-tracking` workflow
instance is archived and deleted by the existing janitor retention decision, the
same Redis transaction now also removes:

- `poll-<instance>.skirmshop`
- `poll-<instance>.skirmshop_metadata`
- that payload key from `text-documents-list`

The deployed image digest is:
`harbor.e-dani.com/homelab/skirmshop-labels-synapse-janitor@sha256:663e0017da491578394c71f1ca10af1a62e727fcda27bd3b87a42245916b596f`.

This is not a manual historical sweep. It does not touch `document-*`, does not
delete non-`poll-tracking` workflow artifacts, and only runs after the janitor's
archive-before-delete and still-deletable re-checks pass.

### Remaining historical policy (not approved yet)

- Expire completed/terminal tracking `poll-*` payload **and** its metadata pair
  after **30-45 days**, after snapshot and legacy segment review.
- Retain active / null-status rows until the tracking workflow closes them.
- Prefer native Redis `EXPIRE`/`PEXPIREAT` set **at write time by the producer**
  (self-cleaning), or a scoped janitor GC pass — not a one-off manual sweep.
  `noeviction` stays; TTL is per-key, not a policy change.

### Read-only dry-run (safe to run after snapshot)

```bash
# READ-ONLY: bucket poll metadata by createdAt age; never print payload values.
MASTER=$(kubectl -n skirmshop get pod -l app=labels-valkey,valkey-role=master \
  -o jsonpath='{.items[0].metadata.name}')
kubectl -n skirmshop exec "$MASTER" -c valkey -- sh -lc '
  valkey-cli -p 6379 --scan --pattern "poll-*.skirmshop_metadata" | while read -r k; do
    # extract ONLY the createdAt field; do not emit the rest of the value
    valkey-cli -p 6379 GET "$k" | grep -o "\"createdAt\":\"[^\"]*\"" || true
  done | sort | head   # aggregate/oldest sample only
'
```

### Non-reference / lifecycle proof (must hold before expiry)

```bash
# READ-ONLY: confirm payload↔metadata pairing count and the single unpaired payload.
# READ-ONLY (Postgres): re-check that closed tracking maps to these poll IDs using
# shipmentNameSegment(shipment_id), not raw shipment_id:
# kubectl -n databases exec postgres-shared-2 -- psql -U postgres -d labels -c \
#   "select count(*) from tracking_poll_attempt;"
# Do NOT expire by age alone; require terminal lifecycle status plus legacy review.
```

Acceptance to leave the historical dry-run gate: a re-capture shows a non-zero,
explainable join between terminal tracking rows and the poll keys marked for
expiry, the 10 legacy/other Redis segments are classified, and the one unpaired
payload is explained.

### Mutating commands — NO EJECUTAR (planning reference only)

> Not approved. Documented only to separate intent from the read-only steps.

```bash
### NO EJECUTAR — poll-* TTL/GC (requires: snapshot verified, ID contract signed,
### lifecycle proof green, batches bounded)
###
### MASTER=... # current master pod
### kubectl -n skirmshop exec "$MASTER" -c valkey -- sh -lc '
###   # For each terminal+aged pair, set TTL on BOTH payload and metadata:
###   # valkey-cli -p 6379 EXPIRE "poll-<id>.skirmshop" 3888000            # ~45d
###   # valkey-cli -p 6379 EXPIRE "poll-<id>.skirmshop_metadata" 3888000
###   :
### '
### # Preferred long-term: producer sets EXPIRE at write time; no manual sweep.
### # Rollback: restore Longhorn snapshot of PVC data, or RESTORE from RDB copy.
```

---

## About `text-documents-list` (do not mistake it for proof of `document-*`)

`text-documents-list` is a single Redis `list` (`230,576` bytes). Despite the name
it is the **current index of `poll`/other `.skirmshop` payloads, not of
`document-*`**. Measured references (report §Reference Report):

```text
LLEN=4143
members pointing at live keys=4143
member families: poll_payload=4007, other_skirmshop_payload_metadata=136
members pointing at document-* keys=0
```

Consequences for both cards:

- `text-documents-list` is **not** evidence about `document-*` retention or
  reachability — its membership excludes `document-*` entirely.
- It **is** a live index of `poll-*.skirmshop` / other `.skirmshop` payloads, so
  Card B must treat it as a reference structure to keep consistent: expiring a
  `poll-*` payload without reconciling this list would create dangling members.
- The name is misleading (`needs_code_fix`): rename/rebuild or document the real
  purpose in a follow-up; do not delete or repurpose it inside Card A or B.

---

## Observability follow-up — labels-valkey memory / growth alert

Status: **implemented in GitOps, pending rollout/series confirmation**

### What was reviewed

Initial review found no `labels-valkey` Prometheus metrics: the StatefulSet only
ran `valkey` and `sentinel`, and the existing `synapse_redis_*` series come from the
**janitor**
(`synapse_redis_workflow_instances_total`, `synapse_redis_orphan_index_entries_total`)
and are per-workflow gauges — they do **not** measure total keyspace or
`used_memory`, and specifically do not cover the `document-*`/`poll-*` growth that
this plan is about. The remediation adds:

- `oliver006/redis_exporter:v1.66.0` sidecar on each `labels-valkey` pod,
  pointing at `redis://localhost:6379`.
- `metrics` port `9121` on `labels-valkey-headless`, labelled
  `metrics.e-dani.com/scrape=true`.
- `VMServiceScrape/labels-valkey`.
- `VMRule` alerts for missing metrics, low scrape target count, exporter down,
  invalid master count, high memory (`redis_memory_used_bytes > 350Mi`), 24h
  memory growth over 50Mi, evictions, and rejected connections.

Post-rollout verification gate: query VictoriaMetrics/VMAlert for live
`redis_up{job="labels-valkey-headless"}` and
`redis_memory_used_bytes{job="labels-valkey-headless"}` before relying on the
alerts operationally. Until the series are observed live, the GitOps change is
deployed but the monitoring outcome is not proven.

---

## Remediation-plan acceptance checklist

### Directives
- [x] Follow scope exactly — only `docs/labels-valkey-remediation-plan-2026-07-01.md`,
      `k8s/synapse-janitor.yaml`, `k8s/monitoring.yaml`, and
      `k8s/valkey-sentinel.yaml` were touched.
      Evidence: `git status --porcelain` after edits.
- [x] No Redis/Postgres key or row mutation is approved or performed by this
      plan. Evidence: live-changing examples remain fenced under `NO EJECUTAR`;
      deployment and rollout validation are handled through GitOps checks.
- [x] Root-cause over workaround: both cards gate on code-fix (age source / ID
      contract) before any delete/TTL. Evidence: Card A §Root-cause posture,
      Card B §blocker.
- [x] No secrets, no payloads, no OCR/tracking content. Evidence: only hashed key
      prefixes and aggregate counts reused from the report.

### Acceptance criteria
- [x] `document-*` runbook includes snapshot, read-only dry-run, non-reference
      proof, rollback, blocking criteria, and mutating commands fenced as
      `NO EJECUTAR`. Evidence: Card A sections above.
- [x] `poll-*` TTL/GC runbook includes the same six elements. Evidence: Card B
      sections above.
- [x] `poll-*` producer ID contract is documented with direct source/runtime
      evidence. Evidence: Card B §Contract proof and current blocker.
- [x] Forward cleanup for future terminal/stale `poll-tracking` artifacts is
      implemented through the janitor, not a manual Redis sweep. Evidence:
      source commit `97df077` and `k8s/synapse-janitor.yaml` image digest
      `sha256:663e0017da491578394c71f1ca10af1a62e727fcda27bd3b87a42245916b596f`.
- [blocked] Historical `poll-*` bulk cleanup remains blocked pending snapshot,
      classification of 10 legacy/other Redis segments, explanation of the one
      unpaired payload, and explicit batch approval. Evidence: Card B blocker.
- [x] Mutating commands are separated from read-only commands and labelled
      `NO EJECUTAR`. Evidence: the `### NO EJECUTAR` fenced blocks in both cards.
- [x] `text-documents-list` documented as the current index of `poll`/other
      `.skirmshop`, explicitly **not** proof of `document-*`. Evidence:
      §"About text-documents-list".
- [x] labels-valkey memory/growth alert path added. Evidence:
      `k8s/valkey-sentinel.yaml` exporter sidecar + metrics Service port and
      `k8s/monitoring.yaml` `VMServiceScrape`/`VMRule` alerts. Runtime proof is
      still a post-rollout verification gate.
