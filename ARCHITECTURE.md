# ARCHITECTURE — k8s-shopify-label-pocharlies

Despliegue del stack de etiquetas/envíos de Skirmshop: routing, sagas, almacenamiento de etiquetas, adaptadores de transportista y workflows Synapse. Código en `pocharlies-org/skirmshop-labels` (fuera de la tanda, con su `CONTRACTS.yaml`).

## Clientes y versiones
- Servicios en ns `skirmshop`: `labels-shopify-app` (app Shopify embebida), `event-gateway`, `label-storage`, `outbox-relay`, `routing-service`, `saga-launcher`, adaptadores `ups`/`correos`/`correos-express`, `event-projector`, `notification-dispatcher`, `tracking-ingestion`, ingest de opt-in WhatsApp/Telegram, Synapse (operator/api/correlator/runner de `ghcr.io/serverlessworkflow`), NATS y Valkey con Sentinel propios. Tronco: `main` (Application `shopify-label`, path `k8s`).

## Dependencias (ambos sentidos)
- Imágenes `harbor.e-dani.com/homelab/skirmshop-labels-*` (construidas desde `skirmshop-labels`). Contratos publicados/consumidos: los de `skirmshop-labels/CONTRACTS.yaml` (enlazar, no duplicar).
- Depende de: Postgres compartido, RabbitMQ, Redis/Valkey, NATS, Synapse API, Shopify (`ymimst-yh.myshopify.com`), Correos/CEX/UPS, Telegram/WhatsApp (opt-in y ops), external-secrets.

## Stack
Kustomize/manifests planos; sin Helm. Config declarativa en `k8s/` (vat-rates, cex-config, correos-surcharges, synapse-workflows/services).

## Componentes compartidos
`k8s/synapse-workflows.yaml` y `synapse-services.yaml` (registro de workflows y servicios en Synapse), `valkey-sentinel.yaml` + `valkey-master-service.yaml`, `nats-cluster.yaml`, `monitoring.yaml`. No usa la base del framework.

## Cómo se construye
Un manifest por responsabilidad; CronJobs: `tracking-poll`, `notification-digest`, `pickup-point-reminder`, `ops-edit-hold-reconciler`, `correos-directory-refresh`, `fuel-freshness`, `vat-sync`, `deep-monitor`. `scripts/pin-wa-activation-digests.sh` fija digests; `scripts/generate-cex-rate-table.py` genera `cex-rate-table.json`. Diseño y decisiones en `plans/sc-231-*.md` y `docs/`.

## Tests y validaciones
`tests/deep-monitor-contract.sh` (contrato del deep-monitor) y `reusable-ci.yml` (yamllint, kustomize, kubeconform; hay un job previo propio en `ci.yml`).

## CI/CD y despliegue
`ci.yml`, `release.yml`, `pr-review.yml`; ArgoCD lee `main`.

## Decisiones y trampas
- `OPS_TELEGRAM_ENABLED=false` y `CORREOS_ENABLED=true` en el Deployment de la app (gates de funcionalidad).
- Informes `docs/*valkey*` documentan retención y limpieza de Valkey (2026-07-01).
- Retirada del stack legacy: `docs/legacy-decommission-2026-05-22.md`.
