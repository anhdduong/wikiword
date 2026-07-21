FROM python:3.12-slim
WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" anthropic

COPY app/ app/
COPY seed/ seed/

ENV WIKIWORD_DB_PATH=/data/wikiword.db
EXPOSE 8000

CMD ["sh", "-c", "python -m app.seed \"$WIKIWORD_DB_PATH\" && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
