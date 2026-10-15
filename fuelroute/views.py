from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.planner import plan_trip
from .services.stations import get_station_index


class RoutePlanView(APIView):
    """POST {"start": "...", "finish": "..."} -> route, fuel stops and total fuel cost."""

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(plan_trip(data["start"], data["finish"]))


class HealthView(APIView):
    """Liveness probe that also confirms the station data loads."""

    throttle_classes = []

    def get(self, request):
        return Response({"status": "ok", "stations_loaded": len(get_station_index())})
