FROM apache/age:release_PG16_1.6.0

# pgvector — available in PGDG apt repo (pre-configured in the base image)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*

# Load AGE automatically for all sessions so callers don't need LOAD 'age'
RUN echo "shared_preload_libraries = 'age'" >> /usr/share/postgresql/postgresql.conf.sample
