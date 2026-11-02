"""URL routes: the JSON API under /api/v1/ and the operator admin under /admin/."""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

from atelier.api.core.sse import job_stream
from atelier.api.core.views import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/jobs/<uuid:job_id>/events", job_stream, name="job-stream"),
    path("api/v1/", api.urls),
]
