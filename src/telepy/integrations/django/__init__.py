"""Django integration.

    INSTALLED_APPS += ["telepy.integrations.django"]
    MIDDLEWARE.insert(0, "telepy.integrations.django.middleware.TelepyMiddleware")
    urlpatterns += [path("telepy/", include("telepy.integrations.django.urls"))]
    TELEPY = {"authorization": lambda request: request.user.is_staff}   # optional
"""
