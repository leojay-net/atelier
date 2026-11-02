# The application layer (ADR-0007, ADR-0017) on top of the reference image, so workers have the
# pinned ffmpeg, LibreOffice and fonts the engine's results are defined against.
#
# Build (after the reference image):
#   docker build -f docker/Dockerfile -t atelier-ref .
#   docker build -f docker/app.Dockerfile -t atelier-app .        (from the repository root)
# Run:  see docker-compose.yml (an API process and one or more worker processes sharing /data).

ARG BASE=atelier-ref
FROM ${BASE}

USER root
COPY --chown=atelier:atelier manage.py /app/manage.py
COPY --chown=atelier:atelier docker/app-entrypoint.sh /app/docker/app-entrypoint.sh
RUN chmod +x /app/docker/app-entrypoint.sh && mkdir -p /data && chown atelier:atelier /data
USER atelier

ENV ATELIER_HOME=/data \
    DJANGO_SETTINGS_MODULE=atelier.api.settings
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["/app/docker/app-entrypoint.sh"]
CMD ["api"]
