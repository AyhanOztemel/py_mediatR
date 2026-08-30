"""Minimal Django ayarlari — sadece demo icin."""
SECRET_KEY = "local-demo-only"
DEBUG = True
ROOT_URLCONF = "examples.django_app.mediator_demo.urls"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
MIDDLEWARE = []
INSTALLED_APPS = []
