"""Django integration.

    INSTALLED_APPS += ["tracefox.integrations.django"]
    MIDDLEWARE.insert(0, "tracefox.integrations.django.middleware.TracefoxMiddleware")
    urlpatterns += [path("tracefox/", include("tracefox.integrations.django.urls"))]
    TRACEFOX = {"authorization": lambda request: request.user.is_staff}   # optional
"""
