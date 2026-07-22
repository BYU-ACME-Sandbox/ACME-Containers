########################  BUILD ARGUMENTS  ####################
ARG PYTHON_VERSION=3.13.14
ARG UV_VERSION=0.11.29
ARG PYTHON_IMAGE_VARIANT=slim-bookworm
ARG LOCK_ARCH

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-${PYTHON_IMAGE_VARIANT}

ARG PYTHON_VERSION
ARG UV_VERSION
ARG PYTHON_IMAGE_VARIANT
ARG LOCK_ARCH
ARG IMAGE_VERSION
ARG SOURCE_URL

########################  ENVIRONMENT  ########################
ENV TZ=America/Denver \
    VIRTUAL_ENV=/opt/acme-venv \
    UV_NO_CACHE=1 \
    DEBIAN_FRONTEND=noninteractive

# Preserve the base image/runtime PATH and only place the shared venv first.
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

LABEL org.opencontainers.image.source="$SOURCE_URL" \
      org.opencontainers.image.title="ACME Core" \
      org.opencontainers.image.version="$IMAGE_VERSION" \
      org.opencontainers.image.description="Shared Python and system layer for ACME course containers"

########################  SYSTEM PACKAGES  ####################
RUN printf '%s\n' \
        'Binary::apt::APT::Keep-Downloaded-Packages "false";' \
        'APT::Keep-Downloaded-Packages "false";' \
        > /etc/apt/apt.conf.d/99no-cache \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates \
        curl \
        wget \
        build-essential \
        cmake \
        libblas-dev \
        liblapack-dev \
        libgl1 \
        libglib2.0-0 \
        ffmpeg \
        git \
        openssh-client \
        unzip \
        sudo \
        passwd \
        vim \
        nano \
        less \
        procps \
        graphviz \
        fontconfig \
        fonts-dejavu-core \
 && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime \
 && echo "$TZ" > /etc/timezone \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /tmp/* /var/tmp/*

########################  UV + GIT  ###########################
COPY --from=uv /uv /uvx /usr/local/bin/

RUN git config --system core.askPass true \
 && git config --system credential.helper cache \
 && git config --system --add safe.directory '*'

########################  NON-ROOT USER  ######################
RUN /usr/sbin/useradd -m -s /bin/bash vscode \
 && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
 && chmod 0440 /etc/sudoers.d/vscode \
 && install -d -o vscode -g vscode \
        "$VIRTUAL_ENV" \
        /opt/acme \
        /opt/acme/locks \
        /opt/acme/constraints \
        /opt/acme/config \
        /opt/acme/scripts \
 && printf '%s\n' \
      'export VIRTUAL_ENV=/opt/acme-venv' \
      'case ":$PATH:" in' \
      '  *":$VIRTUAL_ENV/bin:"*) ;;' \
      '  *) export PATH="$VIRTUAL_ENV/bin:$PATH" ;;' \
      'esac' \
      > /etc/profile.d/acme-venv.sh

########################  PYTHON ENVIRONMENT  #################
COPY --chown=vscode:vscode requirements/locks/core/${LOCK_ARCH}.txt /opt/acme/locks/environment.txt
COPY --chown=vscode:vscode requirements/locks/core/direct-${LOCK_ARCH}.txt /opt/acme/constraints/core-direct.txt
COPY --chown=vscode:vscode config/images.json /opt/acme/config/images.json
COPY --chown=vscode:vscode scripts/verify_core_versions.py scripts/smoke_test.py /opt/acme/scripts/

USER vscode

RUN uv venv --python "/usr/local/bin/python" "$VIRTUAL_ENV" \
 && uv pip sync \
        --python "$VIRTUAL_ENV/bin/python" \
        --no-cache \
        /opt/acme/locks/environment.txt \
 && uv pip check --python "$VIRTUAL_ENV/bin/python" \
 && "$VIRTUAL_ENV/bin/python" /opt/acme/scripts/verify_core_versions.py \
 && printf '{"target":"core","version":"%s","lock_arch":"%s"}\n' \
        "$IMAGE_VERSION" "$LOCK_ARCH" > /opt/acme/image-info.json \
 && rm -rf "$HOME/.cache" /tmp/* /var/tmp/*

WORKDIR /workspaces
CMD ["bash"]
