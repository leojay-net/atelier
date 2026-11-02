# Sandbox for the eval BASELINE: a general coding agent doing the same tasks with plain tools.
#
# It contains what a developer's machine would have for image, video and document work (Python,
# Pillow, NumPy, python-docx, openpyxl, python-pptx, pypdf, pikepdf, a system font, ffmpeg,
# LibreOffice) and none of Atelier: the baseline must not be able to import the engine. The
# Python libraries are pinned to the versions in uv.lock so a difference in results comes from
# the approach, not the library.
#
# Build:  docker build -f docker/baseline.Dockerfile -t atelier-baseline docker/
# It is run by `atelier eval baseline` with no network, a read-only root filesystem, no
# capabilities, resource limits and NO host mounts: files go in and out as tar streams
# (src/atelier/evals/sandbox.py, ADR-0012).

ARG PYTHON_IMAGE=python:3.11-slim-trixie
FROM ${PYTHON_IMAGE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core ffmpeg qpdf \
      libreoffice-core-nogui libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "pillow==12.3.0" "numpy==2.4.6" \
      "python-docx==1.2.0" "openpyxl==3.1.5" "python-pptx==1.0.2" "pypdf==6.19.0" "pikepdf==10.13.0.post1" \
 && useradd --create-home --uid 10001 agent

USER 10001
WORKDIR /work
CMD ["sleep", "infinity"]
