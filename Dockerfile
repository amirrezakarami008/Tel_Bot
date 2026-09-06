FROM postgres:16 AS pgtools
RUN mkdir -p /out \
    && if [ -x /usr/lib/postgresql/16/bin/pg_dump ]; then \
         cp /usr/lib/postgresql/16/bin/pg_dump /out/pg_dump; \
       else \
         cp "$(command -v pg_dump)" /out/pg_dump; \
       fi

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

# پروکسی/آینه برای بیلد (وقتی PyPI از داخل Docker در دسترس نیست)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org files.pythonhosted.org pypi.python.org

ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    ALL_PROXY=${ALL_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    all_proxy=${ALL_PROXY}

# pg_dump هم‌نسخه با سرویس db در docker-compose (postgres:16)
# tzdata برای زمان ۱۲ شب به وقت تهران
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=pgtools /out/pg_dump /usr/local/bin/pg_dump
RUN chmod +x /usr/local/bin/pg_dump

COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i "${PIP_INDEX_URL}" \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    --trusted-host pypi.python.org \
    -r requirements.txt

COPY bot ./bot
COPY gift_files ./gift_files

CMD ["python", "-m", "bot.main"]
