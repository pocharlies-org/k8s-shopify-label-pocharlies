# SC-2092 · reinicio labels-notification-dispatcher y labels-whatsapp-optin-ingest

Comando: `kubectl diff -f <Deployment extraído de k8s/synapse-services.yaml | k8s/whatsapp-optin-ingest.yaml>` (solo lectura, contra el clúster k3s, ns skirmshop).
Resultado: PASS. En ambos Deployments el único cambio es la anotación del pod template; `generation` es contabilidad del servidor.

```
 template:
   metadata:
+      annotations:
+        pocharlies/connector-secret-generation: "2"
       labels:
```

- Anotación solo en `spec.template.metadata` de `labels-notification-dispatcher` y `labels-whatsapp-optin-ingest`: PASS.
- Sin cambio de imagen, env ni recursos: PASS (el diff no muestra ninguna otra línea).
