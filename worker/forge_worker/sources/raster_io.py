"""Small rasterio helpers shared by the raster adapters."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import certifi
import rasterio

from forge_worker.brand import user_agent


@contextmanager
def remote_env() -> Iterator[None]:
    """GDAL settings for reading cloud-optimised GeoTIFFs over HTTPS with few requests."""
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
        GDAL_HTTP_USERAGENT=user_agent(),
        GDAL_HTTP_MAX_RETRY="4",
        GDAL_HTTP_RETRY_DELAY="2",
        GDAL_HTTP_TIMEOUT="120",
        # GDAL's own HTTP client needs to be told where the CA certificates are
        CURL_CA_BUNDLE=os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or certifi.where(),
    ):
        yield
