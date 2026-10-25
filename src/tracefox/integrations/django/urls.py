from django.urls import re_path

from . import views

app_name = "tracefox"

urlpatterns = [
    re_path(r"^(?P<subpath>.*)$", views.dashboard, name="dashboard"),
]
