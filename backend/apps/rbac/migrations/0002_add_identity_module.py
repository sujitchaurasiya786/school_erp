# Generated manually: adds 'identity' to ModuleCode choices on rbac_permissions.
# This is a CharField choices-only change — no schema change in MySQL, just
# the validator at the Django ORM level.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='permission',
            name='module',
            field=models.CharField(
                choices=[
                    ('identity', 'User & Identity Management'),
                    ('admissions', 'Admissions'),
                    ('students', 'Students'),
                    ('academics', 'Academics'),
                    ('attendance', 'Attendance'),
                    ('finance', 'Fees & Finance'),
                    ('examinations', 'Examinations'),
                    ('hrms', 'HRMS & Payroll'),
                    ('library', 'Library'),
                    ('transport', 'Transport'),
                    ('inventory', 'Inventory'),
                    ('helpdesk', 'Helpdesk'),
                    ('analytics', 'Reports & Analytics'),
                    ('rbac', 'Role & Permission Management'),
                    ('school_config', 'School Configuration'),
                    ('audit', 'Audit Logs'),
                ],
                db_index=True,
                max_length=50,
            ),
        ),
    ]

