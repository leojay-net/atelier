# Atelier reference platform (docs/05-determinism.md §5.4).
#
# Golden hashes are only guaranteed inside this image, so everything that can affect output
# bytes is pinned here:
#   * base image: pin by digest before the first release (tag alone can move)
#   * system packages (ffmpeg 7.x, LibreOffice, qpdf…): installed from a fixed
#     snapshot.debian.org date, so `apt-get install` resolves to the same versions forever
#     (falls back to deb.debian.org if the snapshot service is down; see the RUN below)
#   * Python deps: uv.lock, installed with --frozen
#   * fonts: ops accept only the bundled pack in assets/fonts (system fonts are ignored)
#
# Build:  docker build -f docker/Dockerfile -t atelier-ref .
# Test:   docker run --rm --entrypoint uv atelier-ref run --frozen pytest

ARG PYTHON_IMAGE=python:3.11-slim-trixie
FROM ${PYTHON_IMAGE}

ARG DEBIAN_SNAPSHOT=20260901T000000Z

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    TZ=UTC \
    PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    ATELIER_REFERENCE_PLATFORM=1

# snapshot.debian.org is sometimes down for hours (504s). apt-get update only warns on a failed
# fetch, so --error-on=any makes it fail; after three tries the build falls back to the regular
# mirror (same release, newer point versions), says so loudly, and leaves a marker in the image.
RUN set -eu \
 && mv /etc/apt/sources.list.d/debian.sources /tmp/debian.sources \
 && printf '%s\n' \
    "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie main" \
    "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} trixie-security main" \
    > /etc/apt/sources.list \
 && ok=0; for i in 1 2 3; do \
      if apt-get -o Acquire::Retries=3 update --error-on=any; then ok=1; break; fi; sleep 15; \
    done \
 && if [ "$ok" = 0 ]; then \
      echo "WARNING: snapshot.debian.org unavailable; using deb.debian.org (packages not pinned)"; \
      : > /etc/apt/sources.list; mv /tmp/debian.sources /etc/apt/sources.list.d/debian.sources; \
      echo "deb.debian.org" > /etc/atelier-apt-fallback; \
      apt-get -o Acquire::Retries=3 update; \
    fi \
 && rm -f /tmp/debian.sources \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libreoffice-core-nogui libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      qpdf \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 10001 atelier
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra ml

COPY assets/ assets/

# The ML plugins' pinned weights (assets/models/models.json), fetched once at build time: a job
# never touches the network. The layer is cached until the manifest changes. This reads the
# manifest with the standard library only, so it does not depend on the source below.
ENV ATELIER_MODELS=/opt/atelier-models
RUN python - <<'FETCH'
import hashlib, json, pathlib, urllib.request
root = pathlib.Path("/opt/atelier-models")
for name, model in json.loads(pathlib.Path("assets/models/models.json").read_text())["models"].items():
    for f in model["files"]:
        dest = root / name / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(f["url"], timeout=120).read()
        assert hashlib.sha256(data).hexdigest() == f["sha256"], f"{name}/{f['path']} does not match its pin"
        dest.write_bytes(data)
FETCH

COPY src/ src/
COPY tests/ tests/
RUN uv sync --frozen --extra ml \
 && chown -R atelier:atelier /app /opt/atelier-models

ENV ATELIER_ASSETS=/app/assets
USER atelier
RUN ffmpeg -hide_banner -version | head -n1

ENTRYPOINT ["uv", "run", "--frozen", "atelier"]
CMD ["--help"]
