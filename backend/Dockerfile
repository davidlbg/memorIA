FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --create-home memory
COPY app ./app
# Runtime entrypoints import app directly from WORKDIR; dependencies are already
# locked above. Avoid rebuilding/installing a duplicate copy of the source.
COPY migrations ./migrations
COPY alembic.ini ./
USER memory
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
