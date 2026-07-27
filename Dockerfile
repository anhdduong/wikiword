FROM python:3.12-slim
WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" anthropic

COPY app/ app/
COPY seed/ seed/

# Prewarmed word_cache snapshot (regenerate: python -m scripts.warm_cache,
# then cp wikiword.db deploy/prewarmed.db). Baking it in means a host with
# no persistent disk (e.g. Render free tier) starts warm on every restart
# instead of empty; `python -m app.seed` below still runs on top of it to
# keep the affix tables current.
COPY deploy/prewarmed.db /srv/wikiword.db

# Default path lives inside the image (always exists, no volume required) so
# the same image runs unmodified on hosts without persistent storage (e.g.
# Render's free tier); Fly overrides this to the mounted volume path (which
# starts empty and accumulates its own cache over time instead).
ENV WIKIWORD_DB_PATH=/srv/wikiword.db
EXPOSE 8000

# $PORT is injected by platforms like Render; falls back to 8000 (Fly, local).
CMD ["sh", "-c", "python -m app.seed \"$WIKIWORD_DB_PATH\" && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
