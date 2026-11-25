from django.db import migrations
from django.utils import timezone


def backfill_sessions_and_enrollments(apps, schema_editor):
    StudentDailyAttendance = apps.get_model('attendance', 'StudentDailyAttendance')
    AttendanceSession = apps.get_model('attendance', 'AttendanceSession')
    ClassEnrollment = apps.get_model('students', 'ClassEnrollment')

    now = timezone.now()
    sessions_cache = {}

    for rec in StudentDailyAttendance.objects.all().iterator():
        # 1. Resolve or create AttendanceSession as SUBMITTED for existing historical records
        key = (rec.academic_year_id, rec.class_name.strip(), rec.section_name.strip(), rec.date)
        session = sessions_cache.get(key)
        if not session:
            session, _ = AttendanceSession.objects.get_or_create(
                academic_year_id=rec.academic_year_id,
                class_name=rec.class_name.strip(),
                section_name=rec.section_name.strip(),
                date=rec.date,
                defaults={
                    'status': 'SUBMITTED',
                    'marked_by_id': rec.marked_by_id,
                    'submitted_by_id': rec.marked_by_id,
                    'submitted_at': now,
                },
            )
            sessions_cache[key] = session

        updates = []
        if rec.session_id != session.id:
            rec.session_id = session.id
            updates.append('session')

        # 2. Link enrollment ONLY on exact unambiguous match; never guess
        if rec.enrollment_id is None:
            matches = list(
                ClassEnrollment.objects.filter(
                    student_id=rec.student_id,
                    academic_year_id=rec.academic_year_id,
                    class_name__iexact=rec.class_name.strip(),
                    section_name__iexact=rec.section_name.strip(),
                )[:2]
            )
            if len(matches) == 1:
                rec.enrollment_id = matches[0].id
                updates.append('enrollment')

        if updates:
            rec.save(update_fields=updates)


def reverse_backfill(apps, schema_editor):
    StudentDailyAttendance = apps.get_model('attendance', 'StudentDailyAttendance')
    StudentDailyAttendance.objects.update(session=None, enrollment=None)


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0008_studentdailyattendance_enrollment_and_more'),
        ('students', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_sessions_and_enrollments, reverse_backfill),
    ]
