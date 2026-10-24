FROM python:3.12.14-slim
ARG SOURCE_COMMIT
ENV MIGRATION_SOURCE_COMMIT=$SOURCE_COMMIT PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock requirements-postgres.lock pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir -r requirements-postgres.lock && python -m pip install --no-deps --no-build-isolation .
COPY tests ./tests
COPY native_tests ./native_tests
COPY scripts ./scripts
COPY .github ./.github
COPY docs ./docs
COPY Dockerfile compose.postgres.yml .dockerignore ./
CMD ["python", "scripts/verify_native.py", "--workspace", "work/container-private", "--output", "/proof/run"]
