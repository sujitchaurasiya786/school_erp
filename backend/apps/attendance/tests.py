from datetime import date, timedelta
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
    StudentDailyAttendance, AttendanceStatus, WEEKDAY_CHOICES
)
from apps.attendance.services import CalendarService, StudentDailyAttendanceService
from apps.identity.models import User, UserType
from apps.rbac.models import Permission, Role, ScopeType, UserRoleAssignment
from apps.school_config.models import AcademicYear
from apps.students.models import Student, Guardian
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
        self.assertContains(res, "Working Days & Attendance Calendar")

    def test_holidays_web_view_access(self):
        self.client.force_login(self.superuser)
        res = self.client.get(reverse('attendance-holidays'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "School Holidays & Vacation Breaks")

    def test_unauthorized_web_view_redirects(self):
        self.client.force_login(self.no_perm_user)
        res = self.client.get(reverse('attendance-calendar'))
        self.assertEqual(res.status_code, 302)  # Redirects to home-dashboard
