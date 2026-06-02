web: gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers ${WEB_CONCURRENCY:-3} --timeout 60 --access-logfile -
worker: python manage.py runscheduler
release: python manage.py migrate --noinput
