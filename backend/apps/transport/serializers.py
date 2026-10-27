from rest_framework import serializers
from apps.transport.models import Vehicle, Driver, Route, RouteStop, StudentTransportAllocation


class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = '__all__'


class DriverSerializer(serializers.ModelSerializer):
    class Meta:
        model = Driver
        fields = '__all__'


class RouteStopSerializer(serializers.ModelSerializer):
    class Meta:
        model = RouteStop
        fields = '__all__'


class RouteSerializer(serializers.ModelSerializer):
    stops = RouteStopSerializer(many=True, read_only=True)
    vehicle_number = serializers.ReadOnlyField(source='vehicle.registration_number')
    driver_name = serializers.ReadOnlyField(source='driver.name')

    class Meta:
        model = Route
        fields = ['id', 'name', 'vehicle', 'vehicle_number', 'driver', 'driver_name', 'stops']


class StudentTransportAllocationSerializer(serializers.ModelSerializer):
    student_name = serializers.ReadOnlyField(source='student.full_name')
    stop_name = serializers.ReadOnlyField(source='route_stop.stop_name')
    route_name = serializers.ReadOnlyField(source='route_stop.route.name')

    class Meta:
        model = StudentTransportAllocation
        fields = ['id', 'student', 'student_name', 'route_stop', 'stop_name', 'route_name', 'created_at']
