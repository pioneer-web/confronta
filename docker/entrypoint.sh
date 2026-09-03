#!/bin/sh
set -eu

if [ "${DJANGO_RUN_MIGRATIONS:-false}" = "true" ]; then
  python manage.py migrate --noinput
fi

if [ "${DJANGO_BOOTSTRAP_SUPERADMIN:-false}" = "true" ]; then
  python manage.py ensure_initial_superadmin
fi

if [ "${DJANGO_COLLECTSTATIC:-false}" = "true" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
