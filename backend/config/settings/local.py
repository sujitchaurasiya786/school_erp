"""
Local development settings.
"""
from .base import *

DEBUG = True

# In local development, display emails in console if sent
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
