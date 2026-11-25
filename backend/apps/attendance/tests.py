from datetime import date, datetime, timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.academics.models import (
    ClassSubject, SchoolClass, Section, Subject, SubjectType,
    TimetablePeriod, TimetableSlot
)
from apps.academics.services import TimetableService
from apps.attendance.models import (
    Holiday, HolidayType, SaturdayRule, OverrideType,
    WorkingDayConfig, SpecialDayOverride,
    StudentDailyAttendance, AttendanceCorrectionRequest, StaffDailyAttendance, AttendanceStatus,
    StaffApprovalStatus, CheckInMethod, WEEKDAY_CHOICES
)
from apps.attendance.services import (
    CalendarService, StudentDailyAttendanceService,
    StaffDailyAttendanceService, AttendanceReportService
)
from apps.identity.models import User, UserType
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.school_config.models import AcademicYear
from apps.students.models import Student, Guardian, ClassEnrollment
from common.exceptions import BusinessConflictError


class AttendanceTestBase(TestCase):
    """
    Base test class providing common fixtures:
      - Synchronized RBAC permissions
      - Superuser and unprivileged users
      - Active AcademicYear (2027-2028: 2027-04-01 to 2028-03-31)
      - Class, Section, Subject, Teacher, Period for timetable tests
      - Sample Student for attendance tests
    """

    @classmethod
    def setUpTestData(cls):
        call_command('sync_permissions')

        # Academic Year
        cls.ay = AcademicYear.objects.create(
            name="2027-2028",
            start_date=date(2027, 4, 1),
            end_date=date(2028, 3, 31),
            is_current=True,
            is_active=True
        )

        # Superuser
        cls.superuser = User.objects.create_superuser(
            username="admin_super",
            email="admin@school.edu",
            password="Password123!",
            user_type=UserType.STAFF
        )

        # Regular user without permissions
        cls.no_perm_user = User.objects.create_user(
            username="regular_staff",
            email="regular@school.edu",
            password="Password123!",
            user_type=UserType.STAFF
        )

        # Teacher user
        cls.teacher = User.objects.create_user(
            username="teacher_sharma",
            email="sharma@school.edu",
            password="Password123!",
            user_type=UserType.STAFF,
            first_name="Ramesh",
            last_name="Sharma"
        )

        # Student & Guardian
        cls.guardian = Guardian.objects.create(
            first_name="Rajesh",
            last_name="Verma",
            relationship="Father",
            phone="9876543210"
        )
        cls.student = Student.objects.create(
            admission_number="ADM-2027-001",
            first_name="Aarav",
            last_name="Verma",
            date_of_birth=date(2012, 8, 15),
            gender="M",
            primary_guardian=cls.guardian,
            address="123 School Lane",
            admission_date=date(2027, 4, 1)
        )

        # Academics fixtures for timetable testing
        cls.school_class = SchoolClass.objects.create(
            name="Class 10",
            code="CLS-10",
            order=10
        )
        cls.section = Section.objects.create(
            school_class=cls.school_class,
            name="A"
        )
        cls.subject = Subject.objects.create(
            name="Mathematics",
            code="MATH-10",
            subject_type=SubjectType.COMPULSORY
        )
        cls.class_sub = ClassSubject.objects.create(
            section=cls.section,
            subject=cls.subject,
            teacher=cls.teacher
        )
        cls.period = TimetablePeriod.objects.create(
            period_number=1,
            name="Period 1",
            start_time="08:30:00",
            end_time="09:15:00",
            is_break=False
        )
        cls.enrollment = ClassEnrollment.objects.create(
            student=cls.student,
            academic_year=cls.ay,
            class_name="Class 10",
            section_name="A",
            roll_number=1
        )

    def create_user_with_permissions(self, username, perms):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@school.edu",
            password="Password123!",
            user_type=UserType.STAFF
        )
        role = Role.objects.create(name=f"Role for {username}", code=f"ROLE_{username.upper()}")
        for p_code in perms:
            perm = Permission.objects.get(codename=p_code)
            role.permissions.add(perm)
        UserRoleAssignment.objects.create(
            user=user, role=role, scope_type=ScopeType.GLOBAL, is_active=True
        )
        return user


# ==============================================================================
# 1. WorkingDayConfig Model & Validation Tests
# ==============================================================================
class WorkingDayConfigModelTests(AttendanceTestBase):

    def test_default_config_created_or_present(self):
        config, created = WorkingDayConfig.objects.get_or_create(academic_year=self.ay)
        self.assertTrue(config.monday)
        self.assertTrue(config.tuesday)
        self.assertTrue(config.wednesday)
        self.assertTrue(config.thursday)
        self.assertTrue(config.friday)
        self.assertFalse(config.sunday)
        self.assertEqual(config.saturday_rule, SaturdayRule.ALL_WORKING)

    def test_onetoone_constraint_per_academic_year(self):
        WorkingDayConfig.objects.get_or_create(academic_year=self.ay)
        with self.assertRaises(Exception):
            WorkingDayConfig.objects.create(academic_year=self.ay, monday=False)

    def test_configurable_saturday_rules(self):
        config, _ = WorkingDayConfig.objects.get_or_create(academic_year=self.ay)
        for rule in [SaturdayRule.ALL_WORKING, SaturdayRule.ALL_OFF, SaturdayRule.SECOND_FOURTH_OFF]:
            config.saturday_rule = rule
            config.save()
            config.refresh_from_db()
            self.assertEqual(config.saturday_rule, rule)


# ==============================================================================
# 2. Holiday Model & Vacation Break Tests
# ==============================================================================
class HolidayModelTests(AttendanceTestBase):

    def test_create_holiday_within_boundaries(self):
        h = Holiday.objects.create(
            academic_year=self.ay,
            title="Independence Day",
            date=date(2027, 8, 15),
            holiday_type=HolidayType.GAZETTED,
            is_national_holiday=True
        )
        h.clean()
        self.assertIsNotNone(h.pk)

    def test_holiday_outside_academic_year_raises_validation_error(self):
        h = Holiday(
            academic_year=self.ay,
            title="Outside Holiday",
            date=date(2026, 12, 25),  # 2026 is outside 2027-2028
            holiday_type=HolidayType.GAZETTED
        )
        with self.assertRaises(ValidationError):
            h.clean()

    def test_vacation_break_atomic_creation(self):
        holidays = CalendarService.create_vacation_break(
            academic_year=self.ay,
            title="Diwali Break",
            start_date=date(2027, 11, 10),
            end_date=date(2027, 11, 14),
            holiday_type=HolidayType.VACATION,
            description="Festival of lights vacation"
        )
        self.assertEqual(len(holidays), 5)
        self.assertEqual(
            Holiday.objects.filter(academic_year=self.ay, title__startswith="Diwali Break").count(),
            5
        )

    def test_vacation_break_conflict_rollback(self):
        # Pre-create a holiday inside the range
        Holiday.objects.create(
            academic_year=self.ay,
            title="Existing Single Holiday",
            date=date(2027, 12, 25)
        )
        with self.assertRaises(BusinessConflictError):
            CalendarService.create_vacation_break(
                academic_year=self.ay,
                title="Winter Vacation",
                start_date=date(2027, 12, 24),
                end_date=date(2027, 12, 30)
            )
        # Verify no new holidays were created from the failed vacation break
        self.assertFalse(Holiday.objects.filter(title__startswith="Winter Vacation").exists())


# ==============================================================================
# 3. SpecialDayOverride Model Tests
# ==============================================================================
class SpecialDayOverrideModelTests(AttendanceTestBase):

    def test_special_working_day_requires_timetable_weekday(self):
        override = SpecialDayOverride(
            academic_year=self.ay,
            date=date(2027, 5, 2),  # Sunday
            is_working=True,
            override_type=OverrideType.SPECIAL_WORKING,
            timetable_weekday=None,  # Missing timetable weekday
            reason="Compensatory working Sunday"
        )
        with self.assertRaises(ValidationError) as ctx:
            override.clean()
        self.assertIn('timetable_weekday', ctx.exception.message_dict)

    def test_emergency_closure_does_not_require_timetable_weekday(self):
        override = SpecialDayOverride(
            academic_year=self.ay,
            date=date(2027, 7, 15),
            is_working=False,
            override_type=OverrideType.EMERGENCY_CLOSURE,
            timetable_weekday=None,
            reason="Severe Weather / Heavy Rains"
        )
        override.clean()
        override.save()
        self.assertIsNotNone(override.pk)

    def test_override_outside_academic_year_raises_validation_error(self):
        override = SpecialDayOverride(
            academic_year=self.ay,
            date=date(2026, 3, 1),
            is_working=True,
            timetable_weekday=1,
            reason="Outside year"
        )
        with self.assertRaises(ValidationError):
            override.clean()


# ==============================================================================
# 4. CalendarService Resolution Precedence Tests
# ==============================================================================
class CalendarServiceResolutionTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        # Ensure fresh config for tests
        WorkingDayConfig.objects.filter(academic_year=self.ay).delete()
        self.config = WorkingDayConfig.objects.create(
            academic_year=self.ay,
            monday=True,
            tuesday=True,
            wednesday=True,
            thursday=True,
            friday=True,
            sunday=False,
            saturday_rule=SaturdayRule.ALL_WORKING
        )

    def test_precedence_1_special_day_override_overrules_holiday(self):
        target_date = date(2027, 10, 2)  # Gandhi Jayanti
        Holiday.objects.create(
            academic_year=self.ay,
            title="Gandhi Jayanti",
            date=target_date,
            is_national_holiday=True
        )
        # Without override, it is a Holiday
        status_before = CalendarService.get_day_status(self.ay, target_date)
        self.assertFalse(status_before['is_working'])
        self.assertEqual(status_before['type'], 'HOLIDAY')

        # Add SpecialDayOverride declaring it as working (using Monday timetable)
        SpecialDayOverride.objects.create(
            academic_year=self.ay,
            date=target_date,
            is_working=True,
            override_type=OverrideType.SPECIAL_WORKING,
            timetable_weekday=1,
            reason="Special Cleanliness Drive & Half Day Classes"
        )
        status_after = CalendarService.get_day_status(self.ay, target_date)
        self.assertTrue(status_after['is_working'])
        self.assertEqual(status_after['type'], 'SPECIAL_WORKING')
        self.assertEqual(status_after['timetable_weekday'], 1)

    def test_precedence_2_holiday_overrules_regular_working_day(self):
        # 2027-08-15 is Sunday in 2027. Let's use 2027-08-16 (Monday).
        mon_date = date(2027, 8, 16)
        self.assertTrue(CalendarService.is_working_day(self.ay, mon_date))

        Holiday.objects.create(
            academic_year=self.ay,
            title="Local Festival",
            date=mon_date
        )
        status = CalendarService.get_day_status(self.ay, mon_date)
        self.assertFalse(status['is_working'])
        self.assertEqual(status['type'], 'HOLIDAY')

    def test_saturday_policy_second_fourth_off(self):
        self.config.saturday_rule = SaturdayRule.SECOND_FOURTH_OFF
        self.config.save()

        # In August 2027:
        # Aug 1 = Sun, Aug 7 = 1st Sat
        # Aug 14 = 2nd Sat
        # Aug 21 = 3rd Sat
        # Aug 28 = 4th Sat
        sat_1 = date(2027, 8, 7)
        sat_2 = date(2027, 8, 14)
        sat_3 = date(2027, 8, 21)
        sat_4 = date(2027, 8, 28)

        # 1st and 3rd Saturdays are working
        self.assertTrue(CalendarService.is_working_day(self.ay, sat_1))
        self.assertEqual(CalendarService.get_day_status(self.ay, sat_1)['timetable_weekday'], 6)
        self.assertTrue(CalendarService.is_working_day(self.ay, sat_3))

        # 2nd and 4th Saturdays are weekly off
        st_2 = CalendarService.get_day_status(self.ay, sat_2)
        self.assertFalse(st_2['is_working'])
        self.assertEqual(st_2['type'], 'WEEKEND_OFF')
        self.assertIn("2nd Saturday Off", st_2['reason'])

        st_4 = CalendarService.get_day_status(self.ay, sat_4)
        self.assertFalse(st_4['is_working'])
        self.assertEqual(st_4['type'], 'WEEKEND_OFF')
        self.assertIn("4th Saturday Off", st_4['reason'])

    def test_saturday_policy_all_off(self):
        self.config.saturday_rule = SaturdayRule.ALL_OFF
        self.config.save()
        sat = date(2027, 8, 7)
        self.assertFalse(CalendarService.is_working_day(self.ay, sat))

    def test_sunday_weekly_off_and_special_working_override(self):
        sun_date = date(2027, 8, 8)
        self.assertFalse(CalendarService.is_working_day(self.ay, sun_date))

        # Add override for special working Sunday
        SpecialDayOverride.objects.create(
            academic_year=self.ay,
            date=sun_date,
            is_working=True,
            override_type=OverrideType.SPECIAL_WORKING,
            timetable_weekday=2,  # Tuesday timetable
            reason="Sports Day Preparation"
        )
        status = CalendarService.get_day_status(self.ay, sun_date)
        self.assertTrue(status['is_working'])
        self.assertEqual(status['timetable_weekday'], 2)

    def test_get_working_days_in_range_batch(self):
        start = date(2027, 8, 1)  # Sun
        end = date(2027, 8, 7)    # Sat
        # Under ALL_WORKING: Mon, Tue, Wed, Thu, Fri, Sat = 6 working days
        working_days = CalendarService.get_working_days_in_range(self.ay, start, end)
        self.assertEqual(len(working_days), 6)
        self.assertNotIn(start, working_days)  # Sunday excluded
        self.assertIn(end, working_days)      # Saturday included

    def test_get_active_timetable_days(self):
        # Mon-Sat active
        self.assertEqual(CalendarService.get_active_timetable_days(self.ay), [1, 2, 3, 4, 5, 6])

        # Enable Sunday
        self.config.sunday = True
        self.config.save()
        self.assertEqual(CalendarService.get_active_timetable_days(self.ay), [1, 2, 3, 4, 5, 6, 7])

        # Disable Saturday
        self.config.saturday_rule = SaturdayRule.ALL_OFF
        self.config.save()
        self.assertEqual(CalendarService.get_active_timetable_days(self.ay), [1, 2, 3, 4, 5, 7])


# ==============================================================================
# 5. Academics Timetable Calendar Integration Tests
# ==============================================================================
class AcademicsTimetableCalendarIntegrationTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        WorkingDayConfig.objects.filter(academic_year=self.ay).delete()
        self.config = WorkingDayConfig.objects.create(
            academic_year=self.ay,
            monday=True, tuesday=True, wednesday=True, thursday=True, friday=True,
            sunday=False,
            saturday_rule=SaturdayRule.ALL_WORKING
        )

    def test_cannot_schedule_slot_on_inactive_day(self):
        # Sunday (day 7) is inactive by default
        with self.assertRaises(ValidationError) as ctx:
            TimetableService.create_slot(
                academic_year_id=self.ay.pk,
                day_of_week=7,
                period_id=self.period.pk,
                class_subject_id=self.class_sub.pk
            )
        self.assertIn('day_of_week', ctx.exception.message_dict)

    def test_can_schedule_sunday_slot_when_sunday_is_active(self):
        self.config.sunday = True
        self.config.save()

        slot = TimetableService.create_slot(
            academic_year_id=self.ay.pk,
            day_of_week=7,
            period_id=self.period.pk,
            class_subject_id=self.class_sub.pk
        )
        self.assertIsNotNone(slot.pk)
        self.assertEqual(slot.day_of_week, 7)

    def test_matrix_filtering_respects_active_days(self):
        # By default, days should be 1 to 6 (Mon to Sat)
        matrix = TimetableService.get_section_timetable_matrix(self.section.pk, self.ay.pk)
        active_codes = [code for code, name in matrix['days']]
        self.assertEqual(active_codes, [1, 2, 3, 4, 5, 6])

        # Enable Sunday, disable Saturday
        self.config.sunday = True
        self.config.saturday_rule = SaturdayRule.ALL_OFF
        self.config.save()

        matrix2 = TimetableService.get_section_timetable_matrix(self.section.pk, self.ay.pk)
        active_codes2 = [code for code, name in matrix2['days']]
        self.assertEqual(active_codes2, [1, 2, 3, 4, 5, 7])

    def test_get_special_date_timetable_reuses_regular_schedule(self):
        # Create a slot on Monday (day 1)
        slot = TimetableService.create_slot(
            academic_year_id=self.ay.pk,
            day_of_week=1,
            period_id=self.period.pk,
            class_subject_id=self.class_sub.pk
        )

        # On a Sunday with SpecialDayOverride mapping to Monday (day 1)
        sun_date = date(2027, 9, 5)
        SpecialDayOverride.objects.create(
            academic_year=self.ay,
            date=sun_date,
            is_working=True,
            override_type=OverrideType.SPECIAL_WORKING,
            timetable_weekday=1,
            reason="Special Monday Timetable Execution on Sunday"
        )

        tt_data = TimetableService.get_special_date_timetable(sun_date, academic_year_id=self.ay.pk)
        self.assertTrue(tt_data['is_working'])
        self.assertEqual(tt_data['timetable_weekday'], 1)
        self.assertEqual(len(tt_data['slots']), 1)
        self.assertEqual(tt_data['slots'][0].pk, slot.pk)


# ==============================================================================
# 6. Attendance Marking Calendar Integration Tests
# ==============================================================================
class AttendanceMarkingCalendarIntegrationTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        WorkingDayConfig.objects.filter(academic_year=self.ay).delete()
        self.config = WorkingDayConfig.objects.create(
            academic_year=self.ay,
            monday=True, tuesday=True, wednesday=True, thursday=True, friday=True,
            sunday=False,
            saturday_rule=SaturdayRule.ALL_WORKING
        )

    def test_mark_attendance_on_regular_working_day_succeeds(self):
        regular_monday = date(2027, 5, 3)
        rec = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=regular_monday,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.PRESENT
        )
        self.assertIsNotNone(rec.pk)
        self.assertEqual(rec.status, AttendanceStatus.PRESENT)

    def test_mark_attendance_on_holiday_rejected(self):
        holiday_date = date(2027, 8, 15)
        Holiday.objects.create(
            academic_year=self.ay,
            title="Independence Day",
            date=holiday_date
        )
        with self.assertRaises(ValidationError) as ctx:
            StudentDailyAttendanceService.mark_attendance(
                student_id=self.student.pk,
                academic_year_id=self.ay.pk,
                attendance_date=holiday_date,
                class_name="Class 10",
                section_name="A"
            )
        self.assertIn('date', ctx.exception.message_dict)

    def test_mark_attendance_on_weekly_off_rejected(self):
        sunday_date = date(2027, 5, 9)
        with self.assertRaises(ValidationError) as ctx:
            StudentDailyAttendanceService.mark_attendance(
                student_id=self.student.pk,
                academic_year_id=self.ay.pk,
                attendance_date=sunday_date,
                class_name="Class 10",
                section_name="A"
            )
        self.assertIn('date', ctx.exception.message_dict)

    def test_mark_attendance_on_holiday_with_special_override_succeeds(self):
        holiday_date = date(2027, 10, 2)
        Holiday.objects.create(
            academic_year=self.ay,
            title="Gandhi Jayanti",
            date=holiday_date
        )
        # Create override
        SpecialDayOverride.objects.create(
            academic_year=self.ay,
            date=holiday_date,
            is_working=True,
            timetable_weekday=1,
            reason="Special Cleanliness Drive Working Session"
        )

        rec = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=holiday_date,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.PRESENT
        )
        self.assertIsNotNone(rec.pk)

    def test_bulk_attendance_marking_with_calendar_validation(self):
        working_tuesday = date(2027, 5, 4)
        records = [
            {
                'student_id': self.student.pk,
                'academic_year_id': self.ay.pk,
                'date': working_tuesday,
                'class_name': 'Class 10',
                'section_name': 'A',
                'status': 'PRESENT',
                'remarks': 'On time'
            }
        ]
        created_ids = StudentDailyAttendanceService.mark_bulk_attendance(records)
        self.assertEqual(len(created_ids), 1)


# ==============================================================================
# 7. Calendar & Holiday REST API Tests
# ==============================================================================
class CalendarAndHolidayAPITests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.superuser)

    def test_holiday_crud_api(self):
        # Create
        res = self.client.post(
            reverse('holiday-list'),
            {
                'academic_year': self.ay.pk,
                'title': 'Republic Day',
                'date': '2028-01-26',
                'is_national_holiday': True,
                'holiday_type': 'GAZETTED'
            },
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        h_id = res.data['id']

        # Retrieve
        res = self.client.get(reverse('holiday-detail', kwargs={'pk': h_id}))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['title'], 'Republic Day')

        # Update
        res = self.client.patch(
            reverse('holiday-detail', kwargs={'pk': h_id}),
            {'description': 'Celebrated at school ground'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Delete
        res = self.client.delete(reverse('holiday-detail', kwargs={'pk': h_id}))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_vacation_break_action_api(self):
        res = self.client.post(
            f"{reverse('holiday-list')}vacation-break/",
            {
                'academic_year': self.ay.pk,
                'title': 'Autumn Break',
                'start_date': '2027-10-15',
                'end_date': '2027-10-18',
                'holiday_type': 'VACATION',
                'description': 'Mid-term break'
            },
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data), 4)

    def test_working_day_config_api(self):
        config, _ = WorkingDayConfig.objects.get_or_create(academic_year=self.ay)
        res = self.client.get(reverse('working-day-config-detail', kwargs={'pk': config.pk}))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Update Saturday policy
        res = self.client.patch(
            reverse('working-day-config-detail', kwargs={'pk': config.pk}),
            {'saturday_rule': 'SECOND_FOURTH_OFF'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['saturday_rule'], 'SECOND_FOURTH_OFF')

    def test_special_day_override_api(self):
        res = self.client.post(
            reverse('special-day-override-list'),
            {
                'academic_year': self.ay.pk,
                'date': '2027-06-06',  # Sunday
                'is_working': True,
                'override_type': 'SPECIAL_WORKING',
                'timetable_weekday': 1,
                'reason': 'Exam Revision Class'
            },
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        o_id = res.data['id']
        self.assertEqual(res.data['timetable_weekday_display'], 'Monday')

        # Cleanup
        self.client.delete(reverse('special-day-override-detail', kwargs={'pk': o_id}))

    def test_calendar_resolution_endpoints(self):
        # Day status
        res = self.client.get(
            f"{reverse('calendar-resolution-list')}day-status/?academic_year={self.ay.pk}&date=2027-05-03"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_working'])

        # Summary
        res = self.client.get(
            f"{reverse('calendar-resolution-list')}summary/?academic_year={self.ay.pk}&start_date=2027-05-01&end_date=2027-05-31"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('working_days_count', res.data)
        self.assertIn('total_calendar_days', res.data)

        # Active days
        res = self.client.get(
            f"{reverse('calendar-resolution-list')}active-days/?academic_year={self.ay.pk}"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('active_day_codes', res.data)


# ==============================================================================
# 8. Dynamic RBAC Permissions Enforcement Tests
# ==============================================================================
class CalendarRBACAPITests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        self.view_user = self.create_user_with_permissions(
            'cal_viewer', ['attendance.calendar.view', 'attendance.holiday.view']
        )
        self.manage_user = self.create_user_with_permissions(
            'cal_manager', [
                'attendance.calendar.view', 'attendance.calendar.manage',
                'attendance.holiday.view', 'attendance.holiday.manage'
            ]
        )
        self.client = APIClient()

    def test_view_only_user_cannot_mutate(self):
        self.client.force_authenticate(user=self.view_user)

        # Cannot create holiday
        res = self.client.post(
            reverse('holiday-list'),
            {
                'academic_year': self.ay.pk,
                'title': 'Test Holiday',
                'date': '2027-07-20'
            },
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Cannot create override
        res = self.client.post(
            reverse('special-day-override-list'),
            {
                'academic_year': self.ay.pk,
                'date': '2027-07-25',
                'is_working': False,
                'reason': 'Test'
            },
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Can view holidays
        res = self.client.get(reverse('holiday-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Can view day-status
        res = self.client.get(
            f"{reverse('calendar-resolution-list')}day-status/?academic_year={self.ay.pk}&date=2027-05-03"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_manage_user_can_mutate(self):
        self.client.force_authenticate(user=self.manage_user)

        # Can create holiday
        res = self.client.post(
            reverse('holiday-list'),
            {
                'academic_year': self.ay.pk,
                'title': 'Guru Nanak Jayanti',
                'date': '2027-11-23',
                'holiday_type': 'GAZETTED'
            },
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_unauthorized_user_blocked_completely(self):
        self.client.force_authenticate(user=self.no_perm_user)
        res = self.client.get(reverse('holiday-list'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.get(reverse('working-day-config-list'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_full_access(self):
        self.client.force_authenticate(user=self.superuser)
        res = self.client.get(reverse('holiday-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.get(reverse('working-day-config-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)


# ==============================================================================
# 9. Calendar Dashboard Web Views Tests
# ==============================================================================
class CalendarDashboardWebViewsTests(AttendanceTestBase):

    def test_calendar_web_view_access(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse('attendance-calendar'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "School calendar")
        self.assertContains(res, "Working Days & Attendance Calendar")

    def test_calendar_event_lifecycle(self):
        self.client.force_login(self.superuser)
        # Add event
        add_res = self.client.post(
            reverse('attendance-calendar'),
            data={
                'action': 'add_event',
                'title': 'Sports Day',
                'event_type': 'EVENT',
                'start_date': '2026-11-20',
                'end_date': '2026-11-20',
                'note': 'Annual Sports Festival',
                'month': '2026-11',
            },
            follow=True
        )
        self.assertEqual(add_res.status_code, 200)
        self.assertContains(add_res, "Sports Day")
        from apps.attendance.models import CalendarEvent
        event = CalendarEvent.objects.filter(title='Sports Day').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.note, 'Annual Sports Festival')

        # Edit event
        edit_res = self.client.post(
            reverse('attendance-calendar'),
            data={
                'action': 'edit_event',
                'event_id': event.pk,
                'title': 'Sports Day Grand Finale',
                'event_type': 'EVENT',
                'start_date': '2026-11-20',
                'end_date': '2026-11-21',
                'note': 'Extended celebration',
                'month': '2026-11',
            },
            follow=True
        )
        self.assertEqual(edit_res.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.title, 'Sports Day Grand Finale')

        # Delete event
        del_res = self.client.post(
            reverse('attendance-calendar'),
            data={
                'action': 'delete_event',
                'event_id': event.pk,
                'month': '2026-11',
            },
            follow=True
        )
        self.assertEqual(del_res.status_code, 200)
        self.assertFalse(CalendarEvent.objects.filter(title='Sports Day Grand Finale').exists())

    def test_holidays_web_view_access(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse('attendance-holidays'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "School Holidays & Vacation Breaks")

    def test_unauthorized_web_view_redirects(self):
        self.client.force_login(self.no_perm_user)
        res = self.client.get(reverse('attendance-calendar'))
        self.assertEqual(res.status_code, 302)  # Redirects to home-dashboard


# ==============================================================================
# 10. Daily Attendance Service & Scope Tests
# ==============================================================================
class DailyAttendanceServiceTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        # Create second student enrolled in same section
        self.student2 = Student.objects.create(
            admission_number="ADM-2027-002",
            first_name="Priya",
            last_name="Sharma",
            date_of_birth=date(2013, 2, 10),
            gender="F",
            primary_guardian=self.guardian,
            address="456 School Lane",
            admission_date=date(2027, 4, 1)
        )
        self.enrollment2 = ClassEnrollment.objects.create(
            student=self.student2,
            academic_year=self.ay,
            class_name="Class 10",
            section_name="A",
            roll_number=2
        )

    def test_get_section_roster_with_attendance(self):
        # Target working date: Monday 2027-04-05
        t_date = date(2027, 4, 5)
        data = StudentDailyAttendanceService.get_section_roster_with_attendance(
            academic_year=self.ay,
            class_name="Class 10",
            section_name="A",
            target_date=t_date
        )
        self.assertEqual(data['total_students'], 2)
        self.assertTrue(data['is_working'])
        self.assertFalse(data['is_already_marked'])
        self.assertEqual(len(data['roster']), 2)
        self.assertEqual(data['roster'][0]['roll_number'], 1)
        self.assertEqual(data['roster'][1]['roll_number'], 2)

    def test_mark_individual_attendance_success(self):
        t_date = date(2027, 4, 5)  # Monday
        record = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=t_date,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.PRESENT,
            remarks="On time",
            actor=self.superuser
        )
        self.assertEqual(record.status, AttendanceStatus.PRESENT)
        self.assertEqual(record.remarks, "On time")
        self.assertEqual(record.student, self.student)
        self.assertEqual(record.marked_by, self.superuser)

    def test_mark_attendance_non_working_day_fails(self):
        # 2027-04-04 is Sunday (non-working)
        sunday_date = date(2027, 4, 4)
        with self.assertRaises(ValidationError):
            StudentDailyAttendanceService.mark_attendance(
                student_id=self.student.pk,
                academic_year_id=self.ay.pk,
                attendance_date=sunday_date,
                class_name="Class 10",
                section_name="A",
                status=AttendanceStatus.PRESENT
            )

    def test_mark_attendance_holiday_fails_without_override(self):
        h_date = date(2027, 4, 14)  # Ambedkar Jayanti
        Holiday.objects.create(
            academic_year=self.ay,
            title="Ambedkar Jayanti",
            date=h_date,
            holiday_type=HolidayType.GAZETTED
        )
        with self.assertRaises(ValidationError):
            StudentDailyAttendanceService.mark_attendance(
                student_id=self.student.pk,
                academic_year_id=self.ay.pk,
                attendance_date=h_date,
                class_name="Class 10",
                section_name="A",
                status=AttendanceStatus.PRESENT
            )

    def test_mark_attendance_holiday_with_override_succeeds(self):
        h_date = date(2027, 4, 14)
        Holiday.objects.create(
            academic_year=self.ay,
            title="Ambedkar Jayanti",
            date=h_date,
            holiday_type=HolidayType.GAZETTED
        )
        # Create special working day override
        SpecialDayOverride.objects.create(
            academic_year=self.ay,
            date=h_date,
            is_working=True,
            override_type=OverrideType.SPECIAL_WORKING,
            timetable_weekday=3,  # Wednesday
            reason="Special Examination Day",
            created_by=self.superuser
        )
        record = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=h_date,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.PRESENT
        )
        self.assertEqual(record.status, AttendanceStatus.PRESENT)

    def test_mark_attendance_unenrolled_student_fails(self):
        t_date = date(2027, 4, 5)
        # Attempt to mark attendance in Class 9 Section B where student is not enrolled
        with self.assertRaises(ValidationError):
            StudentDailyAttendanceService.mark_attendance(
                student_id=self.student.pk,
                academic_year_id=self.ay.pk,
                attendance_date=t_date,
                class_name="Class 9",
                section_name="B",
                status=AttendanceStatus.PRESENT
            )

    def test_duplicate_marking_updates_existing_record(self):
        t_date = date(2027, 4, 5)
        rec1 = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=t_date,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.PRESENT,
            remarks="Present initial"
        )
        # Mark again on same date with ABSENT
        rec2 = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=t_date,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.ABSENT,
            remarks="Absent updated"
        )
        self.assertEqual(rec1.pk, rec2.pk)
        self.assertEqual(rec2.status, AttendanceStatus.ABSENT)
        self.assertEqual(rec2.remarks, "Absent updated")
        self.assertEqual(StudentDailyAttendance.objects.filter(student=self.student, date=t_date).count(), 1)

    def test_mark_bulk_attendance_success(self):
        t_date = date(2027, 4, 5)
        records = [
            {
                'student_id': self.student.pk,
                'academic_year_id': self.ay.pk,
                'date': t_date,
                'class_name': "Class 10",
                'section_name': "A",
                'status': AttendanceStatus.PRESENT,
                'remarks': "Regular"
            },
            {
                'student_id': self.student2.pk,
                'academic_year_id': self.ay.pk,
                'date': t_date,
                'class_name': "Class 10",
                'section_name': "A",
                'status': AttendanceStatus.ABSENT,
                'remarks': "Sick leave"
            }
        ]
        ids = StudentDailyAttendanceService.mark_bulk_attendance(records=records, actor=self.superuser)
        self.assertEqual(len(ids), 2)
        self.assertEqual(StudentDailyAttendance.objects.get(student=self.student, date=t_date).status, AttendanceStatus.PRESENT)
        self.assertEqual(StudentDailyAttendance.objects.get(student=self.student2, date=t_date).status, AttendanceStatus.ABSENT)

    def test_validate_user_attendance_scope(self):
        # 1. Superuser has global access
        self.assertTrue(StudentDailyAttendanceService.validate_user_attendance_scope(self.superuser, "Class 10", "A"))

        # 2. Teacher assigned as class_teacher to Section A
        self.section.class_teacher = self.teacher
        self.section.save()
        self.assertTrue(StudentDailyAttendanceService.validate_user_attendance_scope(self.teacher, "Class 10", "A"))

        # 3. Another teacher without assignment or role is blocked
        other_teacher = User.objects.create_user(
            username="other_teacher",
            email="other@school.edu",
            password="Password123!"
        )
        self.assertFalse(StudentDailyAttendanceService.validate_user_attendance_scope(other_teacher, "Class 10", "A"))

        # 4. Scoped role assignment: CLASS scope
        role = Role.objects.create(name='Class 10 Scoped Marker', code='class_10_marker')
        role.permissions.add(Permission.objects.get(codename='attendance.record.mark'))
        UserRoleAssignment.objects.create(
            user=other_teacher,
            role=role,
            scope_type=ScopeType.CLASS,
            scope_id="Class 10"
        )
        self.assertTrue(StudentDailyAttendanceService.validate_user_attendance_scope(other_teacher, "Class 10", "A"))
        self.assertFalse(StudentDailyAttendanceService.validate_user_attendance_scope(other_teacher, "Class 9", "A"))


# ==============================================================================
# 11. Attendance Correction Workflow Tests
# ==============================================================================
class AttendanceCorrectionWorkflowTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        # Create marked attendance record
        self.t_date = date(2027, 4, 5)
        self.attendance = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=self.t_date,
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.ABSENT,
            remarks="Marked absent by mistake",
            actor=self.teacher
        )

    def test_submit_correction_request_success(self):
        req = StudentDailyAttendanceService.submit_correction_request(
            attendance_id=self.attendance.pk,
            new_status=AttendanceStatus.PRESENT,
            reason="Student arrived during period 2 with medical note",
            user=self.teacher
        )
        self.assertEqual(req.attendance, self.attendance)
        self.assertEqual(req.new_status, AttendanceStatus.PRESENT)
        self.assertEqual(req.requested_by, self.teacher)
        self.assertIsNone(req.is_approved)

    def test_duplicate_pending_correction_fails(self):
        StudentDailyAttendanceService.submit_correction_request(
            attendance_id=self.attendance.pk,
            new_status=AttendanceStatus.PRESENT,
            reason="First request",
            user=self.teacher
        )
        with self.assertRaises(BusinessConflictError):
            StudentDailyAttendanceService.submit_correction_request(
                attendance_id=self.attendance.pk,
                new_status=AttendanceStatus.HALF_DAY,
                reason="Second request while first is pending",
                user=self.teacher
            )

    def test_review_correction_approve_updates_attendance(self):
        req = StudentDailyAttendanceService.submit_correction_request(
            attendance_id=self.attendance.pk,
            new_status=AttendanceStatus.PRESENT,
            reason="Was present in lab",
            user=self.teacher
        )
        # Approver reviews and approves
        reviewed = StudentDailyAttendanceService.review_correction_request(
            request_id=req.pk,
            is_approved=True,
            review_notes="Verified by Principal",
            user=self.superuser
        )
        self.assertTrue(reviewed.is_approved)
        self.assertEqual(reviewed.reviewed_by, self.superuser)

        # Target StudentDailyAttendance record is updated!
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, AttendanceStatus.PRESENT)
        self.assertIn("Corrected to PRESENT", self.attendance.remarks)

    def test_review_correction_reject_keeps_original_status(self):
        req = StudentDailyAttendanceService.submit_correction_request(
            attendance_id=self.attendance.pk,
            new_status=AttendanceStatus.PRESENT,
            reason="Student claims was present",
            user=self.teacher
        )
        # Approver rejects
        reviewed = StudentDailyAttendanceService.review_correction_request(
            request_id=req.pk,
            is_approved=False,
            review_notes="No evidence found in CCTV",
            user=self.superuser
        )
        self.assertFalse(reviewed.is_approved)

        # Status remains ABSENT
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.status, AttendanceStatus.ABSENT)

    def test_review_already_reviewed_request_fails(self):
        req = StudentDailyAttendanceService.submit_correction_request(
            attendance_id=self.attendance.pk,
            new_status=AttendanceStatus.PRESENT,
            reason="Reason",
            user=self.teacher
        )
        StudentDailyAttendanceService.review_correction_request(
            request_id=req.pk,
            is_approved=True,
            review_notes="Approved",
            user=self.superuser
        )
        with self.assertRaises(BusinessConflictError):
            StudentDailyAttendanceService.review_correction_request(
                request_id=req.pk,
                is_approved=False,
                review_notes="Cannot review twice",
                user=self.superuser
            )


# ==============================================================================
# 12. Daily Attendance & Corrections Web Views Tests
# ==============================================================================
class AttendanceDailyWebViewsTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        self.section.class_teacher = self.teacher
        self.section.save()
        role = Role.objects.create(name="Class Teacher Attendance Role", code="ROLE_TEACHER_ATT")
        for p_code in ['attendance.record.view', 'attendance.record.mark', 'attendance.record.correct']:
            perm = Permission.objects.get(codename=p_code)
            role.permissions.add(perm)
        UserRoleAssignment.objects.create(
            user=self.teacher, role=role, scope_type=ScopeType.GLOBAL, is_active=True
        )

    def test_daily_attendance_view_get_authenticated(self):
        self.client.force_login(self.superuser)
        res = self.client.get(
            reverse('attendance-daily'),
            {
                'year': self.ay.pk,
                'class_id': self.school_class.pk,
                'section_name': 'A',
                'date': '2027-04-05'
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Daily Student Attendance")
        self.assertContains(res, "Aarav")
        self.assertContains(res, "ADM-2027-001")

    def test_daily_attendance_view_get_as_teacher(self):
        # Teacher assigned to section A loads daily attendance without scope_value error
        self.section.class_teacher = self.teacher
        self.section.save()
        self.client.force_login(self.teacher)
        res = self.client.get(
            reverse('attendance-daily'),
            {
                'year': self.ay.pk,
                'class_id': self.school_class.pk,
                'section_name': 'A',
                'date': '2027-04-05'
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Daily Student Attendance")
        self.assertContains(res, "Aarav")

    def test_daily_attendance_view_post_save_success(self):
        self.client.force_login(self.superuser)
        res = self.client.post(
            f"{reverse('attendance-daily')}?year={self.ay.pk}&class_id={self.school_class.pk}&section_name=A&date=2027-04-05",
            {
                'action': 'save_attendance',
                'student_ids': [self.student.pk],
                f'status_{self.student.pk}': 'PRESENT',
                f'remarks_{self.student.pk}': 'All good',
            }
        )
        self.assertEqual(res.status_code, 302)
        rec = StudentDailyAttendance.objects.filter(student=self.student, date='2027-04-05').first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, AttendanceStatus.PRESENT)

    def test_daily_attendance_view_post_blocked_on_non_working_day(self):
        self.client.force_login(self.superuser)
        # 2027-04-04 is Sunday
        res = self.client.post(
            f"{reverse('attendance-daily')}?year={self.ay.pk}&class_id={self.school_class.pk}&section_name=A&date=2027-04-04",
            {
                'action': 'save_attendance',
                'student_ids': [self.student.pk],
                f'status_{self.student.pk}': 'PRESENT',
            }
        )
        self.assertEqual(res.status_code, 302)
        # Attendance must NOT be saved!
        self.assertFalse(StudentDailyAttendance.objects.filter(student=self.student, date='2027-04-04').exists())

    def test_daily_attendance_view_post_submit_correction(self):
        # Create marked record first
        att = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=date(2027, 4, 5),
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.ABSENT,
            actor=self.teacher
        )
        self.client.force_login(self.teacher)
        res = self.client.post(
            f"{reverse('attendance-daily')}?year={self.ay.pk}&class_id={self.school_class.pk}&section_name=A&date=2027-04-05",
            {
                'action': 'submit_correction',
                'attendance_id': att.pk,
                'new_status': 'PRESENT',
                'reason': 'Student arrived late with gate pass'
            }
        )
        self.assertEqual(res.status_code, 302)
        req = AttendanceCorrectionRequest.objects.filter(attendance=att).first()
        self.assertIsNotNone(req)
        self.assertEqual(req.new_status, AttendanceStatus.PRESENT)

    def test_corrections_list_view_get(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse('attendance-corrections'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Attendance Correction Requests")

    def test_corrections_list_view_post_review(self):
        att = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=date(2027, 4, 5),
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.ABSENT,
            actor=self.teacher
        )
        req = StudentDailyAttendanceService.submit_correction_request(
            attendance_id=att.pk,
            new_status=AttendanceStatus.PRESENT,
            reason="Valid medical note",
            user=self.teacher
        )
        self.client.force_login(self.superuser)
        res = self.client.post(
            reverse('attendance-corrections'),
            {
                'action': 'review_correction',
                'request_id': req.pk,
                'decision': 'approve',
                'review_notes': 'Approved by Admin'
            }
        )
        self.assertEqual(res.status_code, 302)
        req.refresh_from_db()
        self.assertTrue(req.is_approved)
        att.refresh_from_db()
        self.assertEqual(att.status, AttendanceStatus.PRESENT)


# ==============================================================================
# 13. Daily Attendance REST API Tests
# ==============================================================================
class AttendanceDailyAPITests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_api_roster_endpoint(self):
        self.client.force_authenticate(user=self.superuser)
        url = reverse('daily-attendance-roster')
        res = self.client.get(url, {
            'academic_year': self.ay.pk,
            'class_name': 'Class 10',
            'section_name': 'A',
            'date': '2027-04-05'
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_working'])
        self.assertEqual(len(res.data['roster']), 1)
        self.assertEqual(res.data['roster'][0]['admission_number'], 'ADM-2027-001')

    def test_api_mark_bulk_endpoint(self):
        self.client.force_authenticate(user=self.superuser)
        url = reverse('daily-attendance-mark-bulk')
        res = self.client.post(url, {
            'records': [
                {
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'class_name': 'Class 10',
                    'section_name': 'A',
                    'date': '2027-04-05',
                    'status': 'PRESENT',
                    'remarks': 'Via API'
                }
            ]
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['updated_count'], 1)

    def test_api_correction_review_endpoint(self):
        att = StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=date(2027, 4, 5),
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.ABSENT,
            actor=self.superuser
        )
        req = StudentDailyAttendanceService.submit_correction_request(
            attendance_id=att.pk,
            new_status=AttendanceStatus.HALF_DAY,
            reason="Left after lunch",
            user=self.superuser
        )
        self.client.force_authenticate(user=self.superuser)
        url = reverse('attendance-correction-review', kwargs={'pk': req.pk})
        res = self.client.post(url, {
            'is_approved': True,
            'review_notes': 'Approved over API'
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        att.refresh_from_db()
        self.assertEqual(att.status, AttendanceStatus.HALF_DAY)


# ==============================================================================
# 14. Staff Daily Attendance Service Tests
# ==============================================================================
class StaffDailyAttendanceServiceTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        self.staff_member = User.objects.create_user(
            username="staff_sunil",
            email="sunil@school.edu",
            password="Password123!",
            user_type=UserType.STAFF,
            first_name="Sunil",
            last_name="Kumar"
        )

    def test_get_staff_roster_with_attendance(self):
        t_date = date(2027, 4, 5)  # Monday
        data = StaffDailyAttendanceService.get_staff_roster_with_attendance(t_date)
        self.assertTrue(data['is_working'])
        self.assertGreaterEqual(data['total_staff'], 2)
        usernames = [r['user'].username for r in data['roster']]
        self.assertIn("teacher_sharma", usernames)
        self.assertIn("staff_sunil", usernames)

    def test_mark_individual_staff_attendance_success(self):
        t_date = date(2027, 4, 5)
        rec = StaffDailyAttendanceService.mark_staff_attendance(
            user_id=self.teacher.pk,
            attendance_date=t_date,
            status=AttendanceStatus.PRESENT,
            remarks="Present in morning assembly",
            actor=self.superuser
        )
        self.assertEqual(rec.status, AttendanceStatus.PRESENT)
        self.assertEqual(rec.remarks, "Present in morning assembly")
        self.assertEqual(rec.user, self.teacher)

    def test_mark_staff_attendance_non_working_day_fails(self):
        sunday_date = date(2027, 4, 4)
        with self.assertRaises(ValidationError):
            StaffDailyAttendanceService.mark_staff_attendance(
                user_id=self.teacher.pk,
                attendance_date=sunday_date,
                status=AttendanceStatus.PRESENT
            )

    def test_duplicate_staff_attendance_updates_record(self):
        t_date = date(2027, 4, 5)
        rec1 = StaffDailyAttendanceService.mark_staff_attendance(
            user_id=self.teacher.pk,
            attendance_date=t_date,
            status=AttendanceStatus.PRESENT,
            remarks="First entry"
        )
        rec2 = StaffDailyAttendanceService.mark_staff_attendance(
            user_id=self.teacher.pk,
            attendance_date=t_date,
            status=AttendanceStatus.LEAVE,
            remarks="Applied casual leave"
        )
        self.assertEqual(rec1.pk, rec2.pk)
        self.assertEqual(rec2.status, AttendanceStatus.LEAVE)
        self.assertEqual(StaffDailyAttendance.objects.filter(user=self.teacher, date=t_date).count(), 1)

    def test_mark_bulk_staff_attendance_success(self):
        t_date = date(2027, 4, 5)
        records = [
            {'user_id': self.teacher.pk, 'status': 'PRESENT', 'remarks': 'On duty'},
            {'user_id': self.staff_member.pk, 'status': 'HALF_DAY', 'remarks': 'Half day leave'}
        ]
        ids = StaffDailyAttendanceService.mark_bulk_staff_attendance(records, t_date, actor=self.superuser)
        self.assertEqual(len(ids), 2)
        self.assertEqual(StaffDailyAttendance.objects.get(user=self.teacher, date=t_date).status, AttendanceStatus.PRESENT)
        self.assertEqual(StaffDailyAttendance.objects.get(user=self.staff_member, date=t_date).status, AttendanceStatus.HALF_DAY)


# ==============================================================================
# 15. Attendance Reports & Shortage Analysis Tests
# ==============================================================================
class AttendanceReportServiceTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        days = [date(2027, 4, 5), date(2027, 4, 6), date(2027, 4, 7), date(2027, 4, 8)]
        for d in days:
            StudentDailyAttendanceService.mark_attendance(
                student_id=self.student.pk,
                academic_year_id=self.ay.pk,
                attendance_date=d,
                class_name="Class 10",
                section_name="A",
                status=AttendanceStatus.PRESENT
            )
        StudentDailyAttendanceService.mark_attendance(
            student_id=self.student.pk,
            academic_year_id=self.ay.pk,
            attendance_date=date(2027, 4, 9),
            class_name="Class 10",
            section_name="A",
            status=AttendanceStatus.ABSENT
        )

    def test_get_student_attendance_summary(self):
        summary = AttendanceReportService.get_student_attendance_summary(
            academic_year=self.ay,
            class_name="Class 10",
            section_name="A",
            start_date="2027-04-05",
            end_date="2027-04-09",
            threshold=75.0
        )
        self.assertEqual(summary['total_working_days'], 5)
        self.assertEqual(len(summary['rows']), 1)
        row = summary['rows'][0]
        self.assertEqual(row['present_days'], 4)
        self.assertEqual(row['absent_days'], 1)
        self.assertEqual(row['percentage'], 80.0)
        self.assertFalse(row['is_shortage'])

    def test_attendance_shortage_alert_flag(self):
        summary = AttendanceReportService.get_student_attendance_summary(
            academic_year=self.ay,
            class_name="Class 10",
            section_name="A",
            start_date="2027-04-05",
            end_date="2027-04-09",
            threshold=85.0
        )
        row = summary['rows'][0]
        self.assertTrue(row['is_shortage'])
        self.assertEqual(summary['shortage_count'], 1)

    def test_get_staff_attendance_summary(self):
        StaffDailyAttendanceService.mark_staff_attendance(
            user_id=self.teacher.pk,
            attendance_date=date(2027, 4, 5),
            status=AttendanceStatus.PRESENT
        )
        summary = AttendanceReportService.get_staff_attendance_summary(
            start_date="2027-04-05",
            end_date="2027-04-09"
        )
        self.assertGreaterEqual(summary['total_staff'], 1)
        teacher_row = next((r for r in summary['rows'] if r['user'] == self.teacher), None)
        self.assertIsNotNone(teacher_row)
        self.assertEqual(teacher_row['present_days'], 1)

    def test_export_csv(self):
        headers = ['Name', 'Attendance %']
        rows = [['Aarav Verma', '80.0%']]
        response = AttendanceReportService.export_csv("test_report.csv", headers, rows)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('Aarav Verma,80.0%', response.content.decode('utf-8'))


# ==============================================================================
# 16. Staff Attendance & Reports Web Views Tests
# ==============================================================================
class StaffAttendanceAndReportsWebViewsTests(AttendanceTestBase):

    def test_staff_attendance_web_view_get(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse('attendance-staff'), {'date': '2027-04-05'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Staff Daily Attendance")
        self.assertContains(res, "teacher_sharma")

    def test_staff_attendance_web_view_post_save(self):
        self.client.force_login(self.superuser)
        res = self.client.post(
            f"{reverse('attendance-staff')}?date=2027-04-05",
            {
                'action': 'save_staff_attendance',
                'user_ids': [self.teacher.pk],
                f'status_{self.teacher.pk}': 'PRESENT',
                f'remarks_{self.teacher.pk}': 'On duty',
            }
        )
        self.assertEqual(res.status_code, 302)
        rec = StaffDailyAttendance.objects.filter(user=self.teacher, date='2027-04-05').first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, AttendanceStatus.PRESENT)

    def test_attendance_reports_web_view_get(self):
        self.client.force_login(self.superuser)
        res = self.client.get(
            reverse('attendance-reports'),
            {
                'report_type': 'student_summary',
                'year': self.ay.pk,
                'class_id': self.school_class.pk,
                'section_name': 'A',
                'start_date': '2027-04-05',
                'end_date': '2027-04-09'
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Attendance Analytics & Reports")
        self.assertContains(res, "Student Section Attendance Report")

    def test_attendance_reports_web_view_csv_export(self):
        self.client.force_login(self.superuser)
        res = self.client.get(
            reverse('attendance-reports'),
            {
                'report_type': 'student_summary',
                'year': self.ay.pk,
                'class_id': self.school_class.pk,
                'section_name': 'A',
                'start_date': '2027-04-05',
                'end_date': '2027-04-09',
                'export': 'csv'
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv')
        self.assertIn('Aarav Verma', res.content.decode('utf-8'))


class StaffSelfAttendanceServiceTests(AttendanceTestBase):
    """
    Tests for teacher/staff self attendance with geolocation radius and approval workflows.
    """

    def setUp(self):
        super().setUp()
        self.today = date(2027, 4, 5)
        from apps.school_config.models import SchoolProfile
        self.profile = SchoolProfile.get_singleton()
        self.profile.latitude = 28.613939
        self.profile.longitude = 77.209021
        self.profile.geo_radius_meters = 500
        self.profile.school_start_time = "08:30:00"
        self.profile.school_end_time = "14:30:00"
        self.profile.save()

    def test_haversine_distance_calculation(self):
        # Distance between identical points is 0
        d0 = StaffDailyAttendanceService.calculate_haversine_distance(
            28.613939, 77.209021, 28.613939, 77.209021
        )
        self.assertEqual(d0, 0.0)

        # Distance to nearby point (~110 meters)
        d_near = StaffDailyAttendanceService.calculate_haversine_distance(
            28.613939, 77.209021, 28.614900, 77.209021
        )
        self.assertIsNotNone(d_near)
        self.assertTrue(50 < d_near < 200)

    def test_self_check_in_within_radius_and_on_time(self):
        with patch('django.utils.timezone.localtime') as mock_now:
            # Set mock time to 08:15:00 (on-time)
            dummy_dt = datetime.combine(self.today, datetime.strptime("08:15:00", "%H:%M:%S").time())
            mock_now.return_value = dummy_dt
            with patch('django.utils.timezone.localdate', return_value=self.today):
                rec = StaffDailyAttendanceService.record_self_attendance(
                    user=self.teacher,
                    action_type='CHECK_IN',
                    latitude=28.613950,
                    longitude=77.209030,
                    selfie_base64="data:image/jpeg;base64,sample_img",
                    actor=self.teacher
                )
                self.assertIsNotNone(rec)
                self.assertTrue(rec.is_within_radius)
                self.assertFalse(rec.is_late_check_in)
                self.assertEqual(rec.approval_status, StaffApprovalStatus.AUTO_APPROVED)
                self.assertEqual(rec.status, AttendanceStatus.PRESENT)

    def test_self_check_in_outside_radius_requires_reason(self):
        with patch('django.utils.timezone.localdate', return_value=self.today):
            # Coordinates 5km away
            with self.assertRaises(ValidationError) as cm:
                StaffDailyAttendanceService.record_self_attendance(
                    user=self.teacher,
                    action_type='CHECK_IN',
                    latitude=28.650000,
                    longitude=77.250000,
                    selfie_base64="data:image/jpeg;base64,sample_img",
                    exception_reason="",
                    actor=self.teacher
                )
            self.assertIn('exception_reason', cm.exception.message_dict)

    def test_self_check_in_outside_radius_with_reason_marks_pending_approval(self):
        with patch('django.utils.timezone.localdate', return_value=self.today):
            rec = StaffDailyAttendanceService.record_self_attendance(
                user=self.teacher,
                action_type='CHECK_IN',
                latitude=28.650000,
                longitude=77.250000,
                selfie_base64="data:image/jpeg;base64,sample_img",
                exception_reason="Field trip to Science Museum with students",
                actor=self.teacher
            )
            self.assertFalse(rec.is_within_radius)
            self.assertEqual(rec.approval_status, StaffApprovalStatus.PENDING_APPROVAL)
            self.assertEqual(rec.exception_reason, "Field trip to Science Museum with students")

    def test_review_self_attendance_approve_and_reject(self):
        with patch('django.utils.timezone.localdate', return_value=self.today):
            rec = StaffDailyAttendanceService.record_self_attendance(
                user=self.teacher,
                action_type='CHECK_IN',
                latitude=28.650000,
                longitude=77.250000,
                selfie_base64="data:image/jpeg;base64,sample_img",
                exception_reason="Traffic delay",
                actor=self.teacher
            )
            self.assertEqual(rec.approval_status, StaffApprovalStatus.PENDING_APPROVAL)

            # Review Approve
            approved_rec = StaffDailyAttendanceService.review_self_attendance(
                attendance_id=rec.pk,
                action='APPROVE',
                review_notes="Approved by Principal Sharma",
                reviewer=self.superuser
            )
            self.assertEqual(approved_rec.approval_status, StaffApprovalStatus.APPROVED)
            self.assertEqual(approved_rec.status, AttendanceStatus.PRESENT)

            # Review Reject
            rejected_rec = StaffDailyAttendanceService.review_self_attendance(
                attendance_id=rec.pk,
                action='REJECT',
                review_notes="Invalid explanation",
                reviewer=self.superuser
            )
            self.assertEqual(rejected_rec.approval_status, StaffApprovalStatus.REJECTED)
            self.assertEqual(rejected_rec.status, AttendanceStatus.ABSENT)


class StaffSelfAttendanceWebViewsTests(AttendanceTestBase):
    """
    Web view test cases for self-attendance marking and admin approvals.
    """

    def test_self_attendance_view_get(self):
        self.client.force_login(self.teacher)
        res = self.client.get(reverse('attendance-self-mark'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Mark My Attendance")
        self.assertContains(res, "Camera Verification")
        self.assertContains(res, "Campus Geo-Fencing")

    def test_self_attendance_view_post_check_in(self):
        self.client.force_login(self.teacher)
        res = self.client.post(
            reverse('attendance-self-mark'),
            {
                'action_type': 'CHECK_IN',
                'latitude': '28.613939',
                'longitude': '77.209021',
                'selfie_image': 'data:image/jpeg;base64,test_snap',
                'exception_reason': 'On campus',
            }
        )
        self.assertEqual(res.status_code, 302)
        rec = StaffDailyAttendance.objects.filter(user=self.teacher, date=date.today()).first()
        self.assertIsNotNone(rec)
        self.assertIsNotNone(rec.check_in_time)

    def test_teacher_blocked_from_staff_attendance_admin_screen(self):
        # Teacher without attendance.staff.view permission should be redirected
        self.client.force_login(self.teacher)
        res = self.client.get(reverse('attendance-staff'))
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse('home-dashboard'))

    def test_admin_can_approve_self_attendance(self):
        # Create pending self attendance
        rec = StaffDailyAttendance.objects.create(
            user=self.teacher,
            date=date.today(),
            status=AttendanceStatus.PRESENT,
            approval_status=StaffApprovalStatus.PENDING_APPROVAL,
            exception_reason="Traffic jam",
            marked_by=self.teacher
        )
        self.client.force_login(self.superuser)
        res = self.client.post(
            reverse('attendance-staff'),
            {
                'action': 'approve_self_attendance',
                'attendance_id': rec.pk,
                'review_notes': 'Principal approved',
            }
        )
        self.assertEqual(res.status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.approval_status, StaffApprovalStatus.APPROVED)


# ==============================================================================
# 14. Attendance Session Lifecycle, Enrollment FK, Promotion & API Tests
# ==============================================================================
class AttendanceLifecycleAndAPICompletionTests(AttendanceTestBase):

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        self.client = APIClient()
        from apps.academics.models import SchoolClass, Section
        from apps.students.models import Guardian, Student, ClassEnrollment
        from apps.rbac.models import Role, UserRoleAssignment, ScopeType, Permission

        self.school_class = SchoolClass.objects.create(name="Class 8", code="CL-08", order=8)
        self.teacher_user = User.objects.create_user(username="teach_lifecycle", email="teach_lifecycle@school.test", password="p", user_type="TEACHER")
        self.other_teacher = User.objects.create_user(username="teach_other", email="teach_other@school.test", password="p", user_type="TEACHER")
        self.section = Section.objects.create(school_class=self.school_class, name="A", class_teacher=self.teacher_user)

        # Grant teacher attendance.record.view + attendance.record.mark
        t_role = Role.objects.create(name="Lifecycle Teacher", code="lifecycle_teacher")
        for codename in ['attendance.record.view', 'attendance.record.mark', 'attendance.record.correct']:
            perm = Permission.objects.get(codename=codename)
            t_role.permissions.add(perm)
        UserRoleAssignment.objects.create(user=self.teacher_user, role=t_role, scope_type=ScopeType.SECTION, scope_id=str(self.section.pk), is_active=True)
        UserRoleAssignment.objects.create(user=self.other_teacher, role=t_role, scope_type=ScopeType.SECTION, scope_id="999999", is_active=True)

        self.guardian = Guardian.objects.create(first_name="Raj", last_name="Kumar", phone="9999900001")
        self.student_user = User.objects.create_user(username="STU-LIFE-01", email="stu_life01@school.test", password="p", user_type="STUDENT")
        self.student = Student.objects.create(
            admission_number="STU-LIFE-01",
            user_account=self.student_user,
            first_name="Aarav",
            last_name="Kumar",
            date_of_birth=date(2013, 5, 1),
            gender="M",
            address="Lucknow",
            admission_date=date(2026, 4, 1),
            primary_guardian=self.guardian,
        )
        self.enrollment = ClassEnrollment.objects.create(
            student=self.student,
            academic_year=self.ay,
            class_name="Class 8",
            section_name="A",
            roll_number=1,
        )
        # Monday inside self.ay
        self.work_date = self.ay.start_date + timedelta(days=(7 - self.ay.start_date.weekday()) % 7)

    def test_draft_edit_submit_lock_and_duplicate_prevention(self):
        from apps.attendance.models import AttendanceSession, AttendanceSessionStatus
        self.client.force_login(self.teacher_user)

        # 1. Save Draft
        draft_res = self.client.post(
            '/api/v1/attendance/daily/save-draft/',
            data={
                'records': [{
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'date': str(self.work_date),
                    'class_name': 'Class 8',
                    'section_name': 'A',
                    'status': 'PRESENT',
                    'remarks': 'On time',
                }]
            },
            format='json',
        )
        self.assertEqual(draft_res.status_code, 200)
        self.assertEqual(draft_res.data['session_status'], 'DRAFT')

        rec = StudentDailyAttendance.objects.get(student=self.student, date=self.work_date)
        self.assertEqual(rec.enrollment_id, self.enrollment.pk)
        self.assertEqual(rec.session.status, AttendanceSessionStatus.DRAFT)

        # 2. Edit Draft before submission
        draft_res_2 = self.client.post(
            '/api/v1/attendance/daily/save-draft/',
            data={
                'records': [{
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'date': str(self.work_date),
                    'class_name': 'Class 8',
                    'section_name': 'A',
                    'status': 'ABSENT',
                    'remarks': 'Updated in draft',
                }]
            },
            format='json',
        )
        self.assertEqual(draft_res_2.status_code, 200)
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'ABSENT')

        # 3. Submit and Lock
        submit_res = self.client.post(
            '/api/v1/attendance/daily/submit/',
            data={
                'academic_year_id': self.ay.pk,
                'class_name': 'Class 8',
                'section_name': 'A',
                'date': str(self.work_date),
            },
            format='json',
        )
        self.assertEqual(submit_res.status_code, 200)
        self.assertTrue(submit_res.data['is_locked'])

        # 4. Duplicate submit / silent overwrite must be blocked with 409
        dup_submit = self.client.post(
            '/api/v1/attendance/daily/submit/',
            data={
                'academic_year_id': self.ay.pk,
                'class_name': 'Class 8',
                'section_name': 'A',
                'date': str(self.work_date),
            },
            format='json',
        )
        self.assertEqual(dup_submit.status_code, 409)

        overwrite_res = self.client.post(
            '/api/v1/attendance/daily/save-draft/',
            data={
                'records': [{
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'date': str(self.work_date),
                    'class_name': 'Class 8',
                    'section_name': 'A',
                    'status': 'PRESENT',
                }]
            },
            format='json',
        )
        self.assertEqual(overwrite_res.status_code, 409)

        # 5. Direct PATCH on locked record blocked
        patch_res = self.client.patch(f'/api/v1/attendance/daily/{rec.pk}/', data={'status': 'PRESENT'}, format='json')
        self.assertEqual(patch_res.status_code, 400)

        # 6. Correction request & admin approval updates locked record
        corr_res = self.client.post(
            '/api/v1/attendance/corrections/',
            data={'attendance': rec.pk, 'new_status': 'PRESENT', 'reason': 'Student arrived with medical slip'},
            format='json',
        )
        self.assertEqual(corr_res.status_code, 201)
        corr_id = corr_res.data['id']

        self.client.force_login(self.superuser)
        rev_res = self.client.post(
            f'/api/v1/attendance/corrections/{corr_id}/review/',
            data={'is_approved': True, 'review_notes': 'Verified'},
            format='json',
        )
        self.assertEqual(rev_res.status_code, 200)
        rec.refresh_from_db()
        self.assertEqual(rec.status, 'PRESENT')

    def test_unauthorized_teacher_and_late_policy_and_invalid_status(self):
        # Unassigned teacher cannot view or mark section
        self.client.force_login(self.other_teacher)
        roster_res = self.client.get(
            f'/api/v1/attendance/daily/roster/?academic_year={self.ay.pk}&class_name=Class%208&section_name=A&date={self.work_date}'
        )
        self.assertEqual(roster_res.status_code, 403)

        # Assigned teacher: invalid status and disabled LATE status rejected
        self.client.force_login(self.teacher_user)
        bad_status = self.client.post(
            '/api/v1/attendance/daily/save-draft/',
            data={
                'records': [{
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'date': str(self.work_date),
                    'class_name': 'Class 8',
                    'section_name': 'A',
                    'status': 'INVALID_STATUS',
                }]
            },
            format='json',
        )
        self.assertEqual(bad_status.status_code, 400)

        late_disabled = self.client.post(
            '/api/v1/attendance/daily/save-draft/',
            data={
                'records': [{
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'date': str(self.work_date),
                    'class_name': 'Class 8',
                    'section_name': 'A',
                    'status': 'LATE',
                }]
            },
            format='json',
        )
        self.assertEqual(late_disabled.status_code, 400)

    def test_historical_attendance_preserved_after_promotion_and_reports(self):
        from apps.school_config.models import AcademicYear
        from apps.students.services import StudentService

        self.client.force_login(self.superuser)
        # Mark & submit attendance in 2026-2027 (Class 8 - A)
        self.client.post(
            '/api/v1/attendance/daily/submit/',
            data={
                'academic_year_id': self.ay.pk,
                'class_name': 'Class 8',
                'section_name': 'A',
                'date': str(self.work_date),
                'records': [{
                    'student_id': self.student.pk,
                    'academic_year_id': self.ay.pk,
                    'date': str(self.work_date),
                    'class_name': 'Class 8',
                    'section_name': 'A',
                    'status': 'ABSENT',
                    'remarks': 'Sick',
                }]
            },
            format='json',
        )

        # Promote student to next academic year Class 9 - B
        next_ay = AcademicYear.objects.create(name="2028-2029", start_date=date(2028, 4, 1), end_date=date(2029, 3, 31))
        StudentService.promote_students(
            target_academic_year=next_ay,
            target_class="Class 9",
            target_section="B",
            student_ids=[self.student.pk],
            roll_numbers={self.student.pk: 12},
            user=self.superuser
        )

        # Verify historical attendance still reflects Class 8 - A (Roll 1)
        hist_res = self.client.get(f'/api/v1/attendance/daily/student-history/?student_id={self.student.pk}')
        self.assertEqual(hist_res.status_code, 200)
        self.assertEqual(len(hist_res.data['records']), 1)
        self.assertEqual(hist_res.data['records'][0]['class_name'], 'Class 8')
        self.assertEqual(hist_res.data['records'][0]['section_name'], 'A')
        self.assertEqual(hist_res.data['records'][0]['roll_number'], 1)

        # Verify Daily, Monthly, and Class Summary APIs
        daily_rep = self.client.get(f'/api/v1/attendance/daily/daily-report/?academic_year={self.ay.pk}&date={self.work_date}')
        self.assertEqual(daily_rep.status_code, 200)
        self.assertEqual(daily_rep.data['summary']['absent'], 1)

        monthly_rep = self.client.get(f'/api/v1/attendance/daily/monthly-report/?academic_year={self.ay.pk}&class_name=Class%208&section_name=A&year=2026&month=4')
        self.assertEqual(monthly_rep.status_code, 200)
        self.assertEqual(len(monthly_rep.data['rows']), 1)

        summary_rep = self.client.get(f'/api/v1/attendance/daily/class-summary/?academic_year={self.ay.pk}&start_date=2026-04-01&end_date=2026-04-30')
        self.assertEqual(summary_rep.status_code, 200)

    def test_private_media_requires_authentication(self):
        # Anonymous user cannot access /media/... directly
        res = self.client.get('/media/student_documents/2026/birth_cert.pdf')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login/', res.url)


