from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("health/", views.HealthView.as_view(), name="health"),
]
