# parser-eval image. Runs on OpenShift with an arbitrary user ID and a read-only root filesystem.
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Point these at the internal mirror when building in the air-gapped network.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    DATA_DIR=/data \
    WORK_DIR=/tmp/parser-eval-work \
    UI_PORT=8080 \
    PROXY_PORT=8081

WORKDIR /opt/parser-eval

COPY requirements.txt .
RUN pip install --index-url "${PIP_INDEX_URL}" ${PIP_TRUSTED_HOST:+--trusted-host "${PIP_TRUSTED_HOST}"} -r requirements.txt

COPY app ./app
COPY static ./static
COPY samples ./samples

# OpenShift runs the container with a random UID in group 0.
RUN mkdir -p /data && chgrp -R 0 /opt/parser-eval /data && chmod -R g=u /opt/parser-eval /data

USER 1001
EXPOSE 8080 8081
VOLUME ["/data"]
CMD ["python", "-m", "app.main"]
