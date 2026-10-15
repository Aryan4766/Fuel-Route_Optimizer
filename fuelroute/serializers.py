from rest_framework import serializers

MAX_LOCATION_LENGTH = 200


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=MAX_LOCATION_LENGTH, trim_whitespace=True)
    finish = serializers.CharField(max_length=MAX_LOCATION_LENGTH, trim_whitespace=True)

    def validate(self, attrs):
        if " ".join(attrs["start"].lower().split()) == " ".join(attrs["finish"].lower().split()):
            raise serializers.ValidationError("'start' and 'finish' must be different locations.")
        return attrs
