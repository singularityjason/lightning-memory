FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY lightning_memory/ lightning_memory/

RUN pip install --no-cache-dir ".[gateway]"

FROM python:3.12-slim

RUN useradd --create-home --shell /bin/bash lm
WORKDIR /home/lm

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/lightning-memory-gateway /usr/local/bin/lightning-memory-gateway

USER lm

EXPOSE 8402

CMD ["lightning-memory-gateway"]
