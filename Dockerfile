ARG APP_BASE_IMAGE=node:22-bookworm-slim
FROM ${APP_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

ARG LARK_CLI_VERSION=1.0.14
ARG CODEX_CLI_VERSION=0.125.0
ARG DEBIAN_APT_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_MIRROR=http://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG NPM_REGISTRY=https://registry.npmjs.org

RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
      -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MIRROR}|g" \
      /etc/apt/sources.list.d/debian.sources

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      openssh-client \
      python3 \
    && rm -rf /var/lib/apt/lists/*

RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install -g \
      @larksuite/cli@${LARK_CLI_VERSION} \
      @openai/codex@${CODEX_CLI_VERSION}

RUN useradd --create-home --uid 1000 app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts

ENV PYTHONPATH=/app/src

USER app

ENTRYPOINT ["python3", "-m", "lark_asr"]
