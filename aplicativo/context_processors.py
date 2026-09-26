from django.conf import settings


def google_oauth(request):
    return {'GOOGLE_OAUTH_ENABLED': settings.GOOGLE_OAUTH_ENABLED}
