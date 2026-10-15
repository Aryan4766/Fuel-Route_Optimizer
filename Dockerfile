FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    CACHE_BACKEND=filebased \
    CACHE_LOCATION=/var/cache/fuelroute

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser \
    && mkdir -p /var/cache/fuelroute \
    && chown -R appuser /var/cache/fuelroute
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=4)" || exit 1

# DJANGO_SECRET_KEY must be supplied at run time, and DJANGO_ALLOWED_HOSTS set to the public host
# name (see README). The file cache is shared by workers. The worker timeout (120s) exceeds the
# worst case of three upstream calls at the default 20s read timeout.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
