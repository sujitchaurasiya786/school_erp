"""
Identity & User Management Service for School ERP.

Handles:
  - Account creation with initial default password ("12345678") and mandatory
    password change flag (must_change_password=True).
  - Transactional user creation with dynamic RBAC role assignment.
  - Mandatory password change with Django password validator enforcement,
    session auth hash preservation, and audit logging.
"""
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.identity.models import User, UserType
from apps.rbac.models import Role, ScopeType, UserRoleAssignment


class IdentityService:
    """
    Central business logic for user management and password policies.
    """
    DEFAULT_INITIAL_PASSWORD = "12345678"

    @classmethod
    @transaction.atomic
    def create_user(
        cls,
        username,
        email,
        actor=None,
        password=None,
        first_name="",
        last_name="",
        user_type=UserType.STAFF,
        phone_number=None,
        role=None,
        scope_type=ScopeType.GLOBAL,
        scope_id="",
        is_active=True,
        must_change_password=True,
        request=None,
        **extra_fields,
    ):
        """
        Creates a new ERP user account.

        Business rules:
          1. Assigns default initial password ("12345678") unless explicitly specified.
          2. Explicitly sets must_change_password = True on newly created accounts.
          3. Hashes password securely via Django's set_password().
          4. Assigns selected dynamic RBAC role inside this database transaction.
          5. Generates immutable audit logs without logging plain passwords.
        """
        username = username.strip()
        email = email.strip()

        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError({"username": f"A user with username '{username}' already exists."})

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError({"email": f"A user with email '{email}' already exists."})

        initial_password = password if password else cls.DEFAULT_INITIAL_PASSWORD

        user = User(
            username=username,
            email=email,
            first_name=first_name.strip() if first_name else "",
            last_name=last_name.strip() if last_name else "",
            user_type=user_type,
            phone_number=phone_number,
            is_active=is_active,
            must_change_password=True,
            **extra_fields,
        )
        user.set_password(initial_password)
        user.save()

        # Audit event 1: User Account Creation
        AuditService.log(
            action=AuditAction.CREATE,
            module="identity",
            entity_type="User",
            entity_id=str(user.pk),
            actor=actor,
            request=request,
            reason="New user account created",
            changes={
                "username": user.username,
                "email": user.email,
                "user_type": user.user_type,
            },
        )

        # Audit event 2: Default Initial Password Assignment
        AuditService.log(
            action=AuditAction.UPDATE,
            module="identity",
            entity_type="User",
            entity_id=str(user.pk),
            actor=actor,
            request=request,
            reason="Initial temporary password assigned with mandatory change requirement",
            changes={"must_change_password": True},
        )

        # Dynamic RBAC Role Assignment (if role provided)
        if role:
            if isinstance(role, (int, str)) and str(role).isdigit():
                role_obj = Role.objects.get(pk=int(role))
            elif isinstance(role, str):
                role_obj = Role.objects.get(code=role)
            else:
                role_obj = role

            assignment = UserRoleAssignment.objects.create(
                user=user,
                role=role_obj,
                scope_type=scope_type or ScopeType.GLOBAL,
                scope_id=str(scope_id or ""),
                is_active=True,
            )

            AuditService.log(
                action=AuditAction.CREATE,
                module="rbac",
                entity_type="UserRoleAssignment",
                entity_id=str(assignment.pk),
                actor=actor,
                request=request,
                reason="Dynamic role assigned on user creation",
                changes={
                    "user": user.username,
                    "role": role_obj.name,
                    "scope_type": scope_type or ScopeType.GLOBAL,
                    "scope_id": str(scope_id or ""),
                },
            )

        return user

    @classmethod
    @transaction.atomic
    def change_password(
        cls,
        user,
        new_password,
        confirm_password=None,
        actor=None,
        request=None,
    ):
        """
        Processes a password change for the given user.

        Validation rules:
          1. Both new_password and confirm_password must match (if confirm is provided).
          2. Password must not match the temporary default initial password ("12345678").
          3. Password must satisfy Django's configured password validators.
          4. Updates must_change_password = False.
          5. Preserves session authentication hash if request is provided.
          6. Records audit event without storing password text.
        """
        if not new_password:
            raise ValidationError({"new_password": "New password is required."})

        if confirm_password is not None and new_password != confirm_password:
            raise ValidationError({"confirm_password": "New password and confirm password do not match."})

        if new_password == cls.DEFAULT_INITIAL_PASSWORD:
            raise ValidationError(
                {"new_password": "New password cannot be the default temporary password. Please choose a stronger password."}
            )

        # Run Django's configured password validators (length, common, numeric, etc.)
        validate_password(new_password, user=user)

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password", "updated_at"])

        if request:
            update_session_auth_hash(request, user)

        AuditService.log(
            action=AuditAction.UPDATE,
            module="identity",
            entity_type="User",
            entity_id=str(user.pk),
            actor=actor or user,
            request=request,
            reason="Mandatory password change completed successfully",
            changes={"must_change_password": {"from": True, "to": False}},
        )

        return user

    @classmethod
    def resolve_user_by_identifier(cls, identifier, password=None):
        """
        Resolves a User instance from multiple possible identifiers:
          1. Direct Username or Email (case-insensitive)
          2. Teacher/Staff Employee ID (StaffProfile.employee_id e.g. EMP-001)
          3. Student Admission Number (Student.admission_number e.g. ADM-2026-0001)
          4. Student Roll Number (ClassEnrollment.roll_number in current or active academic year)
        """
        if not identifier:
            return None

        clean_id = str(identifier).strip()
        from django.db.models import Q

        # 1. Direct username or email match (case-insensitive)
        candidate = User.objects.filter(
            Q(username__iexact=clean_id) | Q(email__iexact=clean_id)
        ).first()
        if candidate:
            return candidate

        # 2. Staff / Teacher by Employee ID
        try:
            from apps.hrms.models import StaffProfile
            staff = StaffProfile.objects.filter(
                employee_id__iexact=clean_id
            ).select_related('user_account').first()
            if staff and staff.user_account:
                return staff.user_account
        except Exception:
            pass

        # 3. Student by Admission Number
        try:
            from apps.students.models import Student
            student = Student.objects.filter(
                admission_number__iexact=clean_id
            ).select_related('user_account').first()
            if student and student.user_account:
                return student.user_account
        except Exception:
            pass

        # 4. Student by Roll Number (digits or e.g. ROLL-05)
        roll_digits = None
        if clean_id.isdigit():
            roll_digits = int(clean_id)
        elif clean_id.upper().startswith(('ROLL-', 'ROLL ', 'R-')):
            tail = clean_id.split('-')[-1].split()[-1]
            if tail.isdigit():
                roll_digits = int(tail)

        if roll_digits is not None:
            try:
                from apps.students.models import ClassEnrollment
                enrollments = list(ClassEnrollment.objects.filter(
                    roll_number=roll_digits,
                    academic_year__is_current=True,
                    student__user_account__isnull=False
                ).select_related('student__user_account'))

                # Fallback to any latest enrollments if no current-year flag set
                if not enrollments:
                    enrollments = list(ClassEnrollment.objects.filter(
                        roll_number=roll_digits,
                        student__user_account__isnull=False
                    ).select_related('student__user_account').order_by('-academic_year__start_date')[:10])

                if len(enrollments) == 1:
                    return enrollments[0].student.user_account
                elif len(enrollments) > 1:
                    # Ambiguous roll number across multiple sections/classes:
                    # Match against password to pick the correct user account
                    if password:
                        for enr in enrollments:
                            u = enr.student.user_account
                            if u and (u.check_password(password) or u.password == password):
                                return u
                    return enrollments[0].student.user_account
            except Exception:
                pass

        return None
