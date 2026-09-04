FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN chmod +x docker/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["docker/entrypoint.sh"]
