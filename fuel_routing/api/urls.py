from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_view, name='home_view'),
    path('api/calculate-path/', views.compute_fuel_route, name='compute_fuel_route'),
]
