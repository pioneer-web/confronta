import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {'1','true','yes','on'}

def env_int(name, default=0):
    raw = os.getenv(name, str(default)).strip()
    return int(raw) if raw else default

def env_list(name, default=''):
    return [x.strip() for x in os.getenv(name, default).split(',') if x.strip()]

DJANGO_ENV = os.getenv('DJANGO_ENV', 'development').strip().lower()
if DJANGO_ENV not in {'development', 'production', 'test'}:
    raise ImproperlyConfigured('DJANGO_ENV deve ser development, production ou test.')

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'unsafe-local-key-change-me')
DEBUG = env_bool('DJANGO_DEBUG', DJANGO_ENV != 'production')
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000')

if DJANGO_ENV == 'production' and DEBUG:
    raise ImproperlyConfigured('DJANGO_DEBUG deve estar desabilitado quando DJANGO_ENV=production.')

INSTALLED_APPS = [
    'django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
    'django.contrib.messages','django.contrib.staticfiles','django.contrib.gis','administracao','aplicativo','billing',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware','aplicativo.middleware.LimiteCorpoRequisicaoMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','administracao.context_processors.administracao_context']}}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
DATABASES = {'default': {'ENGINE':'django.contrib.gis.db.backends.postgis','NAME':os.getenv('POSTGRES_DB','dbconfronta'),'USER':os.getenv('POSTGRES_USER','confronta'),'PASSWORD':os.getenv('POSTGRES_PASSWORD','confronta'),'HOST':os.getenv('POSTGRES_HOST','db'),'PORT':os.getenv('POSTGRES_PORT','5432'),'CONN_MAX_AGE':60}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':8}},
    {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE='pt-br'; TIME_ZONE='America/Recife'; USE_I18N=True; USE_TZ=True
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
AUTH_USER_MODEL='administracao.User'; LOGIN_URL='administracao:login'; LOGIN_REDIRECT_URL='administracao:dashboard'; LOGOUT_REDIRECT_URL='administracao:login'
VAR_DIR=BASE_DIR/'var'; QUARANTINE_DIR=VAR_DIR/'quarantine'; EXTRACTED_DIR=VAR_DIR/'extracted'; IMPORT_INBOX_DIR=Path(os.getenv('IMPORT_INBOX_DIR', str(BASE_DIR/'import_inbox')))
# Storage dos lotes do painel. A V3.3 usa duas áreas fisicamente independentes:
#   - working: área canônica compartilhada por web/worker;
#   - recovery: cópia de segurança no bind mount do import_inbox.
# Assim, uma inconsistência/upgrade do volume de working não faz o arquivo enviado
# desaparecer para o worker. O recovery é temporário e removido após sucesso real.
_batch_storage_env = os.getenv('BATCH_STORAGE_DIR', '').strip() or str(BASE_DIR/'manage_batches')
BATCH_STORAGE_DIR=Path(_batch_storage_env)
BATCH_DIR=BATCH_STORAGE_DIR/'working'
_batch_recovery_env = os.getenv('BATCH_RECOVERY_DIR', '').strip()
BATCH_RECOVERY_DIR=Path(_batch_recovery_env) if _batch_recovery_env else IMPORT_INBOX_DIR/'.manage_batches'/'recovery'
# Volume legado da V3.0–V3.2 é montado somente para recuperação de lotes antigos.
_batch_legacy_env = os.getenv('BATCH_LEGACY_STORAGE_DIR', '').strip()
BATCH_LEGACY_STORAGE_DIR=Path(_batch_legacy_env) if _batch_legacy_env else None
BATCH_LEGACY_DIR=(BATCH_LEGACY_STORAGE_DIR/'working') if BATCH_LEGACY_STORAGE_DIR else None
BATCH_LEGACY_RECOVERY_DIR=(BATCH_LEGACY_STORAGE_DIR/'recovery') if BATCH_LEGACY_STORAGE_DIR else None
for p in (QUARANTINE_DIR, EXTRACTED_DIR, IMPORT_INBOX_DIR, BATCH_STORAGE_DIR, BATCH_DIR, BATCH_RECOVERY_DIR): p.mkdir(parents=True, exist_ok=True)

# SICAR: importação exclusivamente manual. O estado/UF é informado no painel e
# o pipeline continua usando staging, validação, comparação e promoção segura.
MAX_UPLOAD_SIZE_BYTES=env_int('MAX_UPLOAD_SIZE_BYTES',0); MAX_ZIP_ENTRIES=env_int('MAX_ZIP_ENTRIES',10000); MAX_ZIP_EXPANSION_RATIO=env_int('MAX_ZIP_EXPANSION_RATIO',200); MAX_ZIP_UNCOMPRESSED_BYTES=env_int('MAX_ZIP_UNCOMPRESSED_BYTES',0)
STRICT_GEOMETRY_VALIDATION=env_bool('STRICT_GEOMETRY_VALIDATION',True); AUTO_REPAIR_INVALID_GEOMETRIES=env_bool('AUTO_REPAIR_INVALID_GEOMETRIES',True)
ANTIVIRUS_ENABLED=env_bool('ANTIVIRUS_ENABLED',False); REQUIRE_ANTIVIRUS=env_bool('REQUIRE_ANTIVIRUS',False); ANTIVIRUS_COMMAND=os.getenv('ANTIVIRUS_COMMAND','clamscan --no-summary')
FILE_UPLOAD_MAX_MEMORY_SIZE=5*1024*1024; DATA_UPLOAD_MAX_MEMORY_SIZE=None
SECURE_CONTENT_TYPE_NOSNIFF=True; X_FRAME_OPTIONS='DENY'; SESSION_COOKIE_HTTPONLY=True; CSRF_COOKIE_HTTPONLY=True
SESSION_COOKIE_NAME='manage_confronta_sessionid'; CSRF_COOKIE_NAME='manage_confronta_csrftoken'
SECURE_COOKIES=env_bool('DJANGO_SECURE_COOKIES',False); SESSION_COOKIE_SECURE=SECURE_COOKIES; CSRF_COOKIE_SECURE=SECURE_COOKIES; SESSION_COOKIE_SAMESITE='Lax'; CSRF_COOKIE_SAMESITE='Lax'
TRUST_PROXY_HEADERS=env_bool('DJANGO_TRUST_PROXY_HEADERS',False); SECURE_SSL_REDIRECT=env_bool('DJANGO_SECURE_SSL_REDIRECT',False); SECURE_HSTS_SECONDS=env_int('DJANGO_SECURE_HSTS_SECONDS',0)
if TRUST_PROXY_HEADERS: SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
if not DEBUG:
    if SECRET_KEY == 'unsafe-local-key-change-me' or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured('DJANGO_SECRET_KEY deve ter pelo menos 50 caracteres em produção.')
    if not SECURE_COOKIES:
        raise ImproperlyConfigured('DJANGO_SECURE_COOKIES deve estar habilitado em produção.')

if DJANGO_ENV == 'production':
    if not ALLOWED_HOSTS or any(host in {'localhost', '127.0.0.1', '*'} for host in ALLOWED_HOSTS):
        raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS deve conter apenas hosts públicos explícitos em produção.')
    if not CSRF_TRUSTED_ORIGINS or any(
        origin.startswith('http://localhost') or origin.startswith('http://127.0.0.1')
        for origin in CSRF_TRUSTED_ORIGINS
    ):
        raise ImproperlyConfigured('DJANGO_CSRF_TRUSTED_ORIGINS deve conter origens HTTPS públicas em produção.')
LOGGING={'version':1,'disable_existing_loggers':False,'handlers':{'console':{'class':'logging.StreamHandler'}},'root':{'handlers':['console'],'level':'INFO'},'loggers':{'administracao':{'handlers':['console'],'level':'INFO','propagate':False}}}

# Sincronizações automáticas de fontes públicas. O SICAR permanece manual.
SOURCE_AUTOMATION_USER_EMAIL=os.getenv('SOURCE_AUTOMATION_USER_EMAIL', os.getenv('DJANGO_SUPERUSER_EMAIL','')).strip().lower()
SOURCE_DOWNLOAD_TIMEOUT_SECONDS=env_int('SOURCE_DOWNLOAD_TIMEOUT_SECONDS',7200)
SOURCE_HTTP_RETRIES=env_int('SOURCE_HTTP_RETRIES',3)
SOURCE_HTTP_RETRY_BACKOFF_SECONDS=env_int('SOURCE_HTTP_RETRY_BACKOFF_SECONDS',2)

IBAMA_AUTOMATION_ENABLED=env_bool('IBAMA_AUTOMATION_ENABLED',True)
IBAMA_AUTOMATION_HOUR=env_int('IBAMA_AUTOMATION_HOUR',5)
IBAMA_AUTOMATION_MINUTE=env_int('IBAMA_AUTOMATION_MINUTE',0)
# v0.4: downloads em lote oficiais do Dados Abertos IBAMA. PAMGIA foi removido
# do caminho crítico da ingestão por não oferecer comportamento estável para bulk.
IBAMA_TERMO_EMBARGO_URL=os.getenv(
    'IBAMA_TERMO_EMBARGO_URL',
    'https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/termo_embargo/termo_embargo_csv.zip',
).strip()
IBAMA_COORDENADAS_URL=os.getenv(
    'IBAMA_COORDENADAS_URL',
    'https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/coordenadas/coordenadas.csv',
).strip()
IBAMA_HISTORICO_URL=os.getenv(
    'IBAMA_HISTORICO_URL',
    'https://dadosabertos.ibama.gov.br/dados/SIFISC/termo_embargo/termo_embargo_historico/termo_embargo_historico.csv',
).strip()
IBAMA_WKT_API_ENABLED=env_bool('IBAMA_WKT_API_ENABLED',True)
IBAMA_WKT_API_URL_TEMPLATE=os.getenv(
    'IBAMA_WKT_API_URL_TEMPLATE',
    'http://corpgateway-api.ibama.gov.br/sicafiservicecorp/api/v1/public/termo/consultar/embargos/wkt?seqTad={seq_tad}',
).strip()
IBAMA_WKT_API_MAX_REQUESTS=env_int('IBAMA_WKT_API_MAX_REQUESTS',5000)
IBAMA_CSV_CHUNK_SIZE=env_int('IBAMA_CSV_CHUNK_SIZE',5000)
IBAMA_MAX_MAIN_CSV_BYTES=env_int('IBAMA_MAX_MAIN_CSV_BYTES',2147483648)

# Piloto INCRA em Pernambuco. URLs são parametrizadas por UF para expansão futura.
INCRA_AUTOMATION_ENABLED=env_bool('INCRA_AUTOMATION_ENABLED',False)
INCRA_AUTOMATION_HOUR=env_int('INCRA_AUTOMATION_HOUR',5)
INCRA_AUTOMATION_MINUTE=env_int('INCRA_AUTOMATION_MINUTE',30)
INCRA_SIGEF_URL_TEMPLATE=os.getenv(
    'INCRA_SIGEF_URL_TEMPLATE',
    'https://certificacao.incra.gov.br/csv_shp/zip/Sigef%20Brasil_{uf}.zip',
).strip()
INCRA_SNCI_URL_TEMPLATE=os.getenv(
    'INCRA_SNCI_URL_TEMPLATE',
    'https://certificacao.incra.gov.br/csv_shp/zip/Im%C3%B3vel%20certificado%20SNCI%20Brasil_{uf}.zip',
).strip()


# Área cliente / consultas territoriais.
CONFRONTA_COMMERCIAL_CONTACT_URL=os.getenv('CONFRONTA_COMMERCIAL_CONTACT_URL','').strip()
PUBLIC_MAX_REQUEST_BODY_BYTES=env_int('PUBLIC_MAX_REQUEST_BODY_BYTES',65536)
QUERY_GEOMETRY_MAX_UPLOAD_BYTES=env_int('QUERY_GEOMETRY_MAX_UPLOAD_BYTES',5*1024*1024)
QUERY_GEOMETRY_MAX_BODY_BYTES=env_int('QUERY_GEOMETRY_MAX_BODY_BYTES',2*1024*1024)
TERRITORIAL_QUERY_TIMEOUT_MS=env_int('TERRITORIAL_QUERY_TIMEOUT_MS',120000)
CAR_LOOKUP_RATE_LIMIT_5MIN=env_int('CAR_LOOKUP_RATE_LIMIT_5MIN',60)
CAR_LOOKUP_IP_RATE_LIMIT_5MIN=env_int('CAR_LOOKUP_IP_RATE_LIMIT_5MIN',120)


# -----------------------------------------------------------------------------
# ASAAS — assinaturas do CONFRONTA
# -----------------------------------------------------------------------------
ASAAS_ENVIRONMENT=os.getenv('ASAAS_ENVIRONMENT','sandbox').strip().lower()
if ASAAS_ENVIRONMENT not in {'sandbox', 'production'}:
    raise ImproperlyConfigured('ASAAS_ENVIRONMENT deve ser sandbox ou production.')
ASAAS_API_KEY=os.getenv('ASAAS_API_KEY','').strip()
ASAAS_WEBHOOK_TOKEN=os.getenv('ASAAS_WEBHOOK_TOKEN','').strip()
_ASAAS_DEFAULT_BASE_URL = 'https://api-sandbox.asaas.com/v3' if ASAAS_ENVIRONMENT == 'sandbox' else 'https://api.asaas.com/v3'
ASAAS_API_BASE_URL=(os.getenv('ASAAS_API_BASE_URL','').strip() or _ASAAS_DEFAULT_BASE_URL).rstrip('/')
ASAAS_USER_AGENT=os.getenv('ASAAS_USER_AGENT','CONFRONTA/1.0 billing').strip()
ASAAS_HTTP_TIMEOUT=env_int('ASAAS_HTTP_TIMEOUT',25)
ASAAS_CHECKOUT_EXPIRES_MINUTES=env_int('ASAAS_CHECKOUT_EXPIRES_MINUTES',60)
# Base HTTPS pública usada nos callbacks do Checkout. Em desenvolvimento local,
# configure com uma URL de túnel (ex.: https://...trycloudflare.com).
ASAAS_CALLBACK_BASE_URL=os.getenv('ASAAS_CALLBACK_BASE_URL','').strip().rstrip('/')
BILLING_GRACE_DAYS=env_int('BILLING_GRACE_DAYS',5)
