from celery import Celery

celery = Celery(__name__, broker="redis://localhost:6379/0")