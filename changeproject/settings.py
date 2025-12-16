from pathlib import Path
import os
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


# ★修正ポイント: 複雑な分岐を削除し、直接設定を記述します
# セキュリティキーは固定の文字列にすることをお勧めしますが、
# とりあえず動かすためにこのままでも構いません（ただし再起動ごとにログアウトされます）
SECRET_KEY = "django-insecure-@vs5ab!6b(4ea99(we1x6)2qk*c5-yyq(@!=)oboz!4!5c6nx8"

# 本番環境なのでFalse推奨ですが、エラー調査中はTrueでも可
DEBUG = True 

# PythonAnywhereのドメインを許可リストに追加
ALLOWED_HOSTS = ['changerank.pythonanywhere.com', '127.0.0.1', 'localhost']


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    'django.contrib.humanize',
    # Debug toolbar (development) -- disabled for now
    # 'debug_toolbar',
    "change",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Debug toolbar middleware (disabled)
    # 'debug_toolbar.middleware.DebugToolbarMiddleware',
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "changeproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "changeproject.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "ja-JP"

TIME_ZONE = "Asia/Tokyo"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

# 静的ファイルの集約先（PythonAnywhereのWebタブの設定と合わせる）
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# 開発/テスト中にDjangoが静的ファイルを探す場所
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Debug toolbar settings (development convenience)
INTERNAL_IPS = []

# If you want to completely hide the debug toolbar regardless of DEBUG,
# set SHOW_TOOLBAR_CALLBACK to a function that returns False.
DEBUG_TOOLBAR_CONFIG = {
    'INTERCEPT_REDIRECTS': False,
    'SHOW_TOOLBAR_CALLBACK': lambda request: False,
}