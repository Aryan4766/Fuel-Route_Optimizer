"""Settings, configured entirely through environment variables (see .env.example)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a number") from exc


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false")
    SECRET_KEY = "insecure-development-key"  # only reachable with DEBUG on
elif not DEBUG and (SECRET_KEY in {"change-me", "insecure-development-key"} or len(SECRET_KEY) < 32):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is a placeholder or shorter than 32 characters")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "rest_framework",
    "fuelroute",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",  # route geometry is large and compresses ~4x
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# The service is stateless: station data is read from a CSV and results live in the cache.
DATABASES = {}

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("API_THROTTLE_RATE", "60/min")},
    "EXCEPTION_HANDLER": "fuelroute.exceptions.api_exception_handler",
}

# --- Cache -----------------------------------------------------------------------------
_cache_backend = os.environ.get("CACHE_BACKEND", "locmem").lower()
_cache_location = os.environ.get("CACHE_LOCATION", "")
if _cache_backend == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _cache_location or "redis://127.0.0.1:6379/1",
        }
    }
elif _cache_backend == "filebased":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": _cache_location or str(BASE_DIR / ".cache"),
        }
    }
elif _cache_backend == "locmem":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    raise ImproperlyConfigured("CACHE_BACKEND must be one of: locmem, filebased, redis")

GEOCODE_CACHE_TTL = int(env_float("GEOCODE_CACHE_TTL_SECONDS", 30 * 24 * 3600))
ROUTE_CACHE_TTL = int(env_float("ROUTE_CACHE_TTL_SECONDS", 24 * 3600))

# --- Domain configuration --------------------------------------------------------------
VEHICLE_RANGE_MILES = env_float("VEHICLE_RANGE_MILES", 500.0)
VEHICLE_MPG = env_float("VEHICLE_MPG", 10.0)
INITIAL_FUEL_FRACTION = env_float("INITIAL_FUEL_FRACTION", 1.0)
STATION_CORRIDOR_MILES = env_float("STATION_CORRIDOR_MILES", 10.0)
STATIONS_DATA_PATH = Path(os.environ.get("STATIONS_DATA_PATH", BASE_DIR / "data" / "stations_geocoded.csv"))
if not STATIONS_DATA_PATH.is_absolute():
    STATIONS_DATA_PATH = BASE_DIR / STATIONS_DATA_PATH

if VEHICLE_RANGE_MILES <= 0 or VEHICLE_MPG <= 0:
    raise ImproperlyConfigured("VEHICLE_RANGE_MILES and VEHICLE_MPG must be positive")
if not 0 <= INITIAL_FUEL_FRACTION <= 1:
    raise ImproperlyConfigured("INITIAL_FUEL_FRACTION must be between 0 and 1")
if STATION_CORRIDOR_MILES <= 0:
    raise ImproperlyConfigured("STATION_CORRIDOR_MILES must be positive")

# --- External services -----------------------------------------------------------------
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
NOMINATIM_BASE_URL = os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org").rstrip("/")
HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "fuel-route-optimizer/1.0 (assessment project)")
HTTP_TIMEOUT = (
    env_float("HTTP_CONNECT_TIMEOUT_SECONDS", 5.0),
    env_float("HTTP_READ_TIMEOUT_SECONDS", 20.0),
)

# --- Logging ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "loggers": {
        "fuelroute": {
            "handlers": ["console"],
            "level": os.environ.get("LOG_LEVEL", "INFO").upper(),
            "propagate": False,
        },
    },
}
