FROM python:3.12-slim
WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" anthropic

COPY app/ app/
COPY seed/ seed/

# Default path lives inside the image (always exists, no volume required) so
# the same image runs unmodified on hosts without persistent storage (e.g.
# Render's free tier); Fly overrides this to the mounted volume path.
ENV WIKIWORD_DB_PATH=/srv/wikiword.db
EXPOSE 8000

# $PORT is injected by platforms like Render; falls back to 8000 (Fly, local).
CMD ["sh", "-c", "python -m app.seed \"$WIKIWORD_DB_PATH\" && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
