#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py ensure_initial_superadmin

if [ "${DJANGO_COLLECTSTATIC:-false}" = "true" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
