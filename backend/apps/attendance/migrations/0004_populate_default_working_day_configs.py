from django.db import migrations


def populate_default_working_day_configs(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    AcademicYear = apps.get_model('school_config', 'AcademicYear')
    WorkingDayConfig = apps.get_model('attendance', 'WorkingDayConfig')

    for year in AcademicYear.objects.using(db_alias).all():
        if not WorkingDayConfig.objects.using(db_alias).filter(academic_year=year).exists():
            WorkingDayConfig.objects.using(db_alias).create(
                academic_year=year,
                monday=True,
                tuesday=True,
                wednesday=True,
                thursday=True,
                friday=True,
                sunday=False,
                saturday_rule='ALL_WORKING',
                notes='Default 6-day working schedule (All Saturdays working)'
            )


def reverse_default_working_day_configs(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0003_alter_holiday_options_holiday_description_and_more'),
        ('school_config', '0002_alter_academicyear_name'),
    ]

    operations = [
        migrations.RunPython(
            populate_default_working_day_configs,
            reverse_code=reverse_default_working_day_configs
        ),
    ]
