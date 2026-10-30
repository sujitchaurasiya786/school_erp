from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from common.models import TimeStampedModel


class ModuleCode(models.TextChoices):
    IDENTITY = 'identity', _('User & Identity Management')
    ADMISSIONS = 'admissions', _('Admissions')
    STUDENTS = 'students', _('Students')
    ACADEMICS = 'academics', _('Academics')
    ATTENDANCE = 'attendance', _('Attendance')
    FINANCE = 'finance', _('Fees & Finance')
    EXAMINATIONS = 'examinations', _('Examinations')
    HRMS = 'hrms', _('HRMS & Payroll')
    LIBRARY = 'library', _('Library')
    TRANSPORT = 'transport', _('Transport')
    INVENTORY = 'inventory', _('Inventory')
    HELPDESK = 'helpdesk', _('Helpdesk')
    ANALYTICS = 'analytics', _('Reports & Analytics')
    RBAC = 'rbac', _('Role & Permission Management')
    SCHOOL_CONFIG = 'school_config', _('School Configuration')
    AUDIT = 'audit', _('Audit Logs')


class ActionCode(models.TextChoices):
    VIEW = 'view', _('View / Read')
    ADD = 'add', _('Create / Add')
    CHANGE = 'change', _('Update / Edit')
    DELETE = 'delete', _('Delete')
    EXPORT = 'export', _('Export')
    APPROVE = 'approve', _('Approve / Reject')


class ScopeType(models.TextChoices):
    GLOBAL = 'GLOBAL', _('School-wide / Global')
    CLASS = 'CLASS', _('Class-level')
    SECTION = 'SECTION', _('Section-level')
    DEPARTMENT = 'DEPARTMENT', _('Department-level')
    OWN_ONLY = 'OWN_ONLY', _('Self / Assigned Only')


class Permission(TimeStampedModel):
    """
    Fine-grained permission for a specific module and action.
    """
    module = models.CharField(max_length=50, choices=ModuleCode.choices, db_index=True)
    action = models.CharField(max_length=20, choices=ActionCode.choices, db_index=True)
    codename = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'rbac_permissions'
        ordering = ['module', 'action', 'name']
        unique_together = ('module', 'action', 'codename')

    def __str__(self):
        return f"{self.name} [{self.codename}]"


class Role(TimeStampedModel):
    """
    Dynamic role configured by Admin or Principal from the dashboard.
    """
    code = models.SlugField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    is_system_role = models.BooleanField(
        default=False,
        help_text=_("System roles cannot be deleted or have critical permissions removed.")
    )
    permissions = models.ManyToManyField(
        Permission,
        related_name='roles',
        blank=True
    )

    class Meta:
        db_table = 'rbac_roles'
        ordering = ['name']

    def __str__(self):
        return self.name


class UserRoleAssignment(TimeStampedModel):
    """
    Assignment of dynamic roles to users with optional scope limitations.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='role_assignments',
        db_index=True
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='user_assignments',
        db_index=True
    )
    scope_type = models.CharField(
        max_length=20,
        choices=ScopeType.choices,
        default=ScopeType.GLOBAL,
        db_index=True
    )
    scope_id = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        default='',
        help_text=_("Identifier for scope, e.g. section_id or class_id.")
    )

    class Meta:
        db_table = 'rbac_user_roles'
        unique_together = ('user', 'role', 'scope_type', 'scope_id')

    def save(self, *args, **kwargs):
        if self.scope_id is None:
            self.scope_id = ''
        super().save(*args, **kwargs)

    def __str__(self):
        scope_str = f" ({self.scope_type}:{self.scope_id})" if self.scope_id else f" ({self.scope_type})"
        return f"{self.user} -> {self.role.name}{scope_str}"
