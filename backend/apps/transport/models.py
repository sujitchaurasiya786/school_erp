from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class Vehicle(TimeStampedModel):
    registration_number = models.CharField(max_length=30, unique=True, db_index=True)
    vehicle_model = models.CharField(max_length=100)
    seating_capacity = models.PositiveIntegerField(default=40)
    insurance_expiry = models.DateField()
    fitness_expiry = models.DateField()
    permit_expiry = models.DateField()

    class Meta:
        db_table = 'transport_vehicles'

    def __str__(self):
        return f"{self.registration_number} ({self.vehicle_model})"


class Driver(TimeStampedModel):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    license_number = models.CharField(max_length=50, unique=True)
    license_expiry = models.DateField()

    class Meta:
        db_table = 'transport_drivers'

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Route(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        db_table = 'transport_routes'

    def __str__(self):
        return self.name


class RouteStop(TimeStampedModel):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='stops')
    stop_name = models.CharField(max_length=100)
    stop_order = models.PositiveIntegerField(default=1)
    pickup_time = models.TimeField()
    drop_time = models.TimeField()
    monthly_fare = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)

    class Meta:
        db_table = 'transport_route_stops'
        ordering = ['stop_order']

    def __str__(self):
        return f"{self.route.name} - {self.stop_name}"


class StudentTransportAllocation(TimeStampedModel):
    student = models.OneToOneField('students.Student', on_delete=models.CASCADE, related_name='transport_allocation')
    route_stop = models.ForeignKey(RouteStop, on_delete=models.PROTECT, related_name='allocated_students')

    class Meta:
        db_table = 'transport_student_allocations'

    def __str__(self):
        return f"{self.student.admission_number} -> {self.route_stop}"
