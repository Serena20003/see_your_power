FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0
# CDO links Debian ecCodes with CCSDS/libaec support; Python ecCodes is separately pinned.
RUN apt-get update && apt-get install -y --no-install-recommends \
    cdo=2.1.1-1+deb12u1 libeccodes-tools=2.28.0-1 libgdal-dev=3.6.2+dfsg-1+b2 \
    gdal-bin=3.6.2+dfsg-1+b2 g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /pipeline
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY raster_pipeline ./raster_pipeline
COPY tests ./tests
COPY fixtures ./fixtures
RUN cdo -V && grib_ls -V && RASTER_REQUIRE_NATIVE=1 python -m pytest tests/raster -q
ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=4
CMD ["python", "-m", "raster_pipeline.cli", "--help"]
