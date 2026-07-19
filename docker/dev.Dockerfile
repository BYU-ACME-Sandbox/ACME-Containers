ARG CORE_IMAGE=ghcr.io/byu-acme-sandbox/acme-core
ARG CORE_VERSION=latest
FROM ${CORE_IMAGE}:${CORE_VERSION}

ARG LOCK_ARCH
ARG IMAGE_VERSION
ARG SOURCE_URL
ARG SVGO_VERSION=4.0.2

LABEL org.opencontainers.image.source="$SOURCE_URL" \
      org.opencontainers.image.title="ACME Lab Development" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.description="Complete ACME curriculum development and publishing environment"

USER root

#####################  DEV SYSTEM PACKAGES  ###################
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        gfortran \
        pkg-config \
        latexmk \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-pictures \
        texlive-science \
        texlive-fonts-recommended \
        texlive-xetex \
        dvisvgm \
        ghostscript \
        nodejs \
        npm \
 && npm install --global "svgo@${SVGO_VERSION}" \
 && npm cache clean --force \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /root/.npm /tmp/* /var/tmp/*

#####################  DEV PYTHON PACKAGES  ###################
COPY requirements/locks/dev/${LOCK_ARCH}.txt /opt/acme/locks/environment.txt
COPY requirements/locks/core/direct-${LOCK_ARCH}.txt /opt/acme/constraints/core-direct.txt
COPY config/images.json /opt/acme/config/images.json
COPY scripts/verify_core_versions.py scripts/smoke_test.py /opt/acme/scripts/

RUN uv pip sync \
        --python "$VIRTUAL_ENV/bin/python" \
        --no-cache \
        /opt/acme/locks/environment.txt \
 && uv pip check --python "$VIRTUAL_ENV/bin/python" \
 && "$VIRTUAL_ENV/bin/python" /opt/acme/scripts/verify_core_versions.py \
 && printf '{"target":"dev","version":"%s","lock_arch":"%s"}\n' \
        "$IMAGE_VERSION" "$LOCK_ARCH" > /opt/acme/image-info.json \
 && chown -R vscode:vscode "$VIRTUAL_ENV" /opt/acme \
 && rm -rf /root/.cache /tmp/* /var/tmp/*

USER vscode
WORKDIR /workspaces
CMD ["bash"]
