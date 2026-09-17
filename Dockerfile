FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app

COPY package.json /app/package.json
RUN npm install --no-audit --no-fund

COPY frontend /app/frontend
COPY aplicativo /app/aplicativo
COPY administracao /app/administracao

RUN npm run build


FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

# Assets compilados no estágio Node
COPY --from=frontend-builder /app/aplicativo/static/aplicativo/css/tailwind.css /app/aplicativo/static/aplicativo/css/tailwind.css
COPY --from=frontend-builder /app/aplicativo/static/aplicativo/js/vendor/flowbite.min.js /app/aplicativo/static/aplicativo/js/vendor/flowbite.min.js
COPY --from=frontend-builder /app/administracao/static/administracao/vendor /app/administracao/static/administracao/vendor

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /app/var/quarantine /app/var/extracted /app/var/batches /app/import_inbox /app/staticfiles

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
