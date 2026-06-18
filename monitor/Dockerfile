# Order-edit production monitor — tiny image: python stdlib + psql client.
# Built out-of-band and pushed to harbor (pinned by digest in the CronJob),
# like the host-side backup tooling. Source lives here for traceability.
FROM harbor.lan.e-dani.com/cache/library/python:3.12-alpine
RUN apk add --no-cache postgresql-client
COPY order-edit-monitor.py /app/order-edit-monitor.py
USER 65534:65534
ENTRYPOINT ["python3", "/app/order-edit-monitor.py"]
