ARG CORE_IMAGE=ghcr.io/byu-acme-sandbox/acme-core
ARG CORE_VERSION=latest
FROM ${CORE_IMAGE}:${CORE_VERSION}

ARG TARGET
ARG LOCK_ARCH
ARG IMAGE_VERSION
ARG SOURCE_URL

LABEL org.opencontainers.image.source="$SOURCE_URL" \
      org.opencontainers.image.title="ACME ${TARGET}" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.description="ACME course environment for ${TARGET}"

COPY --chown=vscode:vscode requirements/locks/courses/${TARGET}/${LOCK_ARCH}.txt /opt/acme/locks/environment.txt
COPY --chown=vscode:vscode requirements/locks/core/direct-${LOCK_ARCH}.txt /opt/acme/constraints/core-direct.txt
COPY --chown=vscode:vscode config/images.json /opt/acme/config/images.json
COPY --chown=vscode:vscode scripts/verify_core_versions.py scripts/smoke_test.py /opt/acme/scripts/

USER vscode

RUN uv pip sync \
        --python "$VIRTUAL_ENV/bin/python" \
        --no-cache \
        /opt/acme/locks/environment.txt \
 && uv pip check --python "$VIRTUAL_ENV/bin/python" \
 && "$VIRTUAL_ENV/bin/python" /opt/acme/scripts/verify_core_versions.py \
 && printf '{"target":"%s","version":"%s","lock_arch":"%s"}\n' \
        "$TARGET" "$IMAGE_VERSION" "$LOCK_ARCH" > /opt/acme/image-info.json \
 && rm -rf "$HOME/.cache" /tmp/* /var/tmp/*

WORKDIR /workspaces
CMD ["bash"]
