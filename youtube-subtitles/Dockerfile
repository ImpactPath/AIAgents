# Stage 1: build the bgutil PO token provider server (Node). YouTube withholds subtitles from
# many cloud IPs unless yt-dlp can get a PO token; the bgutil yt-dlp plugin fetches them from here.
FROM node:22-bookworm-slim AS bgutil

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch 2.0.0 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /bgutil
RUN cd /bgutil/server && npm ci --no-audit --no-fund && npx tsc && npm prune --omit=dev --no-audit --no-fund

# Stage 2: the app. Same Debian release (bookworm) as the node image, so its binary runs as is.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKEN_TTL=6

# node needs libstdc++ (already in the base image through apt; listed to be explicit).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=bgutil /usr/local/bin/node /usr/local/bin/node
COPY --from=bgutil /bgutil/server /opt/bgutil/server

# Hugging Face Spaces (Docker SDK) runs containers as user id 1000.
RUN useradd --create-home --uid 1000 user

WORKDIR /home/user/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user app ./app
COPY --chown=user:user static ./static
COPY --chown=user:user start.sh ./start.sh
# Strip CR in case the file was checked out with Windows line endings.
RUN sed -i 's/\r$//' start.sh && chmod 0755 start.sh

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

EXPOSE 7860

# start.sh launches the token provider on 127.0.0.1:4416, then uvicorn on ${PORT:-7860}
# (PORT lets Render or any host override it; Hugging Face Spaces uses the 7860 default).
CMD ["/home/user/app/start.sh"]
