FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 ticketbot \
    && useradd --uid 10001 --gid ticketbot --create-home --shell /usr/sbin/nologin ticketbot

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=ticketbot:ticketbot . .
RUN mkdir -p /app/data && chown -R ticketbot:ticketbot /app/data

USER 10001:10001

VOLUME ["/app/data"]

CMD ["python", "main.py"]
