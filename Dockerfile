FROM ghcr.io/flant/shell-operator:v1.21.2
USER root
RUN apk add --no-cache python3
COPY lib/c2i.py /opt/c2i/c2i.py
COPY --chmod=755 hooks/reconcile.py /hooks/reconcile.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
