import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.school_config.models import AcademicYear
from apps.students.models import (
    BonafideCertificate, ClassEnrollment, Guardian, Student,
    StudentDocument, StudentStatus, TransferCertificate
)
from common.exceptions import BusinessConflictError, ResourceNotFoundError, SchoolERPException


class StudentService:
    """
    Business logic for student management, enrollments, promotions,
    sibling management, and certificate issuance (TC and Bonafide).
    """

    @classmethod
    def generate_tc_number(cls, year=None):
        prefix_year = year or timezone.now().year
        count = TransferCertificate.objects.filter(tc_number__startswith=f"TC-{prefix_year}-").count() + 1
        candidate = f"TC-{prefix_year}-{count:04d}"
        while TransferCertificate.objects.filter(tc_number=candidate).exists():
            count += 1
            candidate = f"TC-{prefix_year}-{count:04d}"
        return candidate

    @classmethod
    def generate_bonafide_number(cls, year=None):
        prefix_year = year or timezone.now().year
        count = BonafideCertificate.objects.filter(certificate_number__startswith=f"BON-{prefix_year}-").count() + 1
        candidate = f"BON-{prefix_year}-{count:04d}"
        while BonafideCertificate.objects.filter(certificate_number=candidate).exists():
            count += 1
            candidate = f"BON-{prefix_year}-{count:04d}"
        return candidate

    @classmethod
    @transaction.atomic
    def create_student_profile(cls, admission_number, first_name, last_name, date_of_birth,
                               gender, primary_guardian, address, admission_date=None,
                               blood_group="", city="", state="", pincode="", previous_school="",
                               secondary_guardian=None, academic_year=None, class_name=None,
                               section_name=None, roll_number=None, user=None, request=None,
                               student_email="", **kwargs):
        """
        Creates a new student profile and optional initial enrollment.
        """
        if Student.objects.filter(admission_number=admission_number).exists():
            raise BusinessConflictError(f"A student with admission number '{admission_number}' already exists.")

        adm_date = admission_date or timezone.now().date()
        student = Student(
            admission_number=str(admission_number).strip().upper(),
            first_name=str(first_name).strip(),
            last_name=str(last_name).strip(),
            date_of_birth=date_of_birth,
            gender=gender,
            blood_group=str(blood_group or '').strip(),
            address=str(address).strip(),
            city=str(city or '').strip(),
            state=str(state or '').strip(),
            pincode=str(pincode or '').strip(),
            primary_guardian=primary_guardian,
            secondary_guardian=secondary_guardian,
            admission_date=adm_date,
            previous_school=str(previous_school or '').strip(),
            status=StudentStatus.ACTIVE
        )
        student.clean()
        student.save()

        # Link any existing siblings sharing primary guardian phone
        if primary_guardian and primary_guardian.phone:
            existing_siblings = Student.objects.filter(
                primary_guardian__phone=primary_guardian.phone
            ).exclude(pk=student.pk)
            for sib in existing_siblings:
                student.siblings.add(sib)
                sib.siblings.add(student)

        # Initial class enrollment if provided
        if academic_year and class_name and section_name:
            ClassEnrollment.objects.create(
                student=student,
                academic_year=academic_year,
                class_name=str(class_name).strip(),
                section_name=str(section_name).strip(),
                roll_number=roll_number
            )

        # Auto-provision Identity User account for student login and directory visibility
        cls.provision_student_user_account(
            student=student,
            actor=user,
            request=request,
            preferred_email=student_email or kwargs.get('preferred_email'),
            phone_number=primary_guardian.phone if primary_guardian else None
        )

        AuditService.log(
            action=AuditAction.CREATE,
            module='students',
            entity_type='Student',
            entity_id=str(student.pk),
            actor=user,
            reason=f"Created student profile #{student.admission_number} ({student.full_name})",
            request=request
        )
        return student

    @classmethod
    @transaction.atomic
    def provision_student_user_account(cls, student, actor=None, request=None, preferred_email=None, phone_number=None):
        """
        Creates or links an Identity User account for a student.
        Username: student.admission_number
        UserType: STUDENT
        Default Password: 12345678 (with must_change_password=True)
        Assigns the 'student' RBAC role.
        """
        from apps.identity.models import User, UserType
        from apps.rbac.models import Role, ScopeType, UserRoleAssignment

        if student.user_account:
            return student.user_account

        username = student.admission_number.strip().upper()
        existing_user = User.objects.filter(username__iexact=username).first()
        if existing_user:
            student.user_account = existing_user
            student.save(update_fields=['user_account'])
            return existing_user

        # Unique email resolution
        clean_email = (preferred_email or "").strip().lower()
        if clean_email and not User.objects.filter(email__iexact=clean_email).exists():
            student_email = clean_email
        else:
            sanitized_adm = student.admission_number.strip().lower().replace('/', '-').replace(' ', '')
            student_email = f"{sanitized_adm}@student.school.local"
            counter = 1
            while User.objects.filter(email__iexact=student_email).exists():
                student_email = f"{sanitized_adm}_{counter}@student.school.local"
                counter += 1

        phone = phone_number or (student.primary_guardian.phone if student.primary_guardian else None)

        student_user = User(
            username=username,
            email=student_email,
            first_name=student.first_name,
            last_name=student.last_name,
            user_type=UserType.STUDENT,
            phone_number=phone,
            is_active=True,
            must_change_password=True
        )
        student_user.set_password("12345678")
        student_user.save()

        # Link student role
        student_role = Role.objects.filter(code='student').first()
        if not student_role:
            student_role = Role.objects.filter(name__icontains='Student').first()
        if student_role:
            UserRoleAssignment.objects.get_or_create(
                user=student_user,
                role=student_role,
                scope_type=ScopeType.GLOBAL,
                defaults={'is_active': True}
            )

        student.user_account = student_user
        student.save(update_fields=['user_account'])

        AuditService.log(
            action=AuditAction.CREATE,
            module="identity",
            entity_type="User",
            entity_id=str(student_user.pk),
            actor=actor,
            reason=f"Auto-provisioned student user account for {student.admission_number}",
            changes={
                "username": student_user.username,
                "email": student_user.email,
                "user_type": student_user.user_type,
                "admission_number": student.admission_number
            },
            request=request
        )
        return student_user

    @classmethod
    @transaction.atomic
    def issue_transfer_certificate(cls, student_id, reason, user=None, dues_cleared=True, conduct_remarks="Good", request=None):
        """
        Issues a Transfer Certificate (TC), transitions student status to TC_ISSUED,
        and records the audit event.
        """
        try:
            student = Student.objects.select_for_update().get(id=student_id)
        except Student.DoesNotExist:
            raise ResourceNotFoundError(f"Student with ID {student_id} not found.")

        if hasattr(student, 'transfer_certificate'):
            raise SchoolERPException("Transfer Certificate has already been issued for this student.")

        if not str(reason or '').strip():
            raise ValidationError({'reason': _("Reason for leaving is required to issue a Transfer Certificate.")})

        tc_num = cls.generate_tc_number()
        tc = TransferCertificate.objects.create(
            tc_number=tc_num,
            student=student,
            issue_date=timezone.now().date(),
            reason_for_leaving=str(reason).strip(),
            dues_cleared=bool(dues_cleared),
            conduct_remarks=str(conduct_remarks or 'Good').strip()
        )

        student.status = StudentStatus.TC_ISSUED
        student.save()

        AuditService.log(
            action=AuditAction.TC_ISSUE,
            module='students',
            entity_type='TransferCertificate',
            entity_id=str(tc.id),
            actor=user,
            reason=f"TC #{tc_num} issued for {student.admission_number}: {reason}",
            changes={
                'tc_number': tc_num,
                'student': student.admission_number,
                'dues_cleared': dues_cleared,
                'reason': reason
            },
            request=request
        )
        return tc

    @classmethod
    @transaction.atomic
    def issue_bonafide_certificate(cls, student_id, academic_year, purpose, conduct_remarks="Good", user=None, request=None):
        """
        Issues a Bonafide Certificate for a student for official/administrative purposes.
        """
        try:
            student = Student.objects.get(id=student_id)
        except Student.DoesNotExist:
            raise ResourceNotFoundError(f"Student with ID {student_id} not found.")

        if not str(purpose or '').strip():
            raise ValidationError({'purpose': _("Certificate purpose is required (e.g. Passport, Bank, Visa).")})

        bonafide_num = cls.generate_bonafide_number()
        bonafide = BonafideCertificate.objects.create(
            certificate_number=bonafide_num,
            student=student,
            academic_year=academic_year,
            issue_date=timezone.now().date(),
            purpose=str(purpose).strip(),
            conduct_remarks=str(conduct_remarks or 'Good').strip()
        )

        AuditService.log(
            action=AuditAction.CREATE,
            module='students',
            entity_type='BonafideCertificate',
            entity_id=str(bonafide.id),
            actor=user,
            reason=f"Bonafide certificate #{bonafide_num} issued for {student.admission_number} ({purpose})",
            request=request
        )
        return bonafide

    @classmethod
    @transaction.atomic
    def promote_students(cls, target_academic_year, target_class, target_section, student_ids, user=None, roll_numbers=None, request=None):
        """
        Bulk promotes a list of active students to a target class and section.
        Maintains backward compatibility with initial API implementation.
        """
        promoted = []
        roll_map = roll_numbers or {}

        for student_id in student_ids:
            try:
                student = Student.objects.get(id=student_id, status=StudentStatus.ACTIVE)
            except Student.DoesNotExist:
                continue

            roll_num = roll_map.get(student_id) or roll_map.get(str(student_id))
            enrollment, created = ClassEnrollment.objects.update_or_create(
                student=student,
                academic_year=target_academic_year,
                defaults={
                    'class_name': str(target_class).strip(),
                    'section_name': str(target_section).strip(),
                    'roll_number': roll_num
                }
            )
            promoted.append(enrollment)

        AuditService.log(
            action=AuditAction.PROMOTION,
            module='students',
            entity_type='ClassEnrollment',
            entity_id=f"{target_class}-{target_section}",
            actor=user,
            reason=f"Promoted {len(promoted)} students to {target_class} {target_section} ({target_academic_year.name})",
            request=request
        )
        return promoted

    @classmethod
    @transaction.atomic
    def promote_class_with_retention(cls, source_academic_year, target_academic_year, promotions, user=None, request=None):
        """
        Comprehensive year-end promotion workflow supporting:
          - Promotion to next class/section
          - Detention (retain in same class for new academic year)
          - Exception/leaving handling (Passed out / Withdrawn)
        promotions is a list of dicts:
          {
            'student_id': int,
            'action': 'PROMOTE' | 'DETAIN' | 'PASSED_OUT' | 'WITHDRAWN',
            'target_class': str,
            'target_section': str,
            'roll_number': int | None
          }
        """
        results = {
            'promoted': 0,
            'detained': 0,
            'passed_out': 0,
            'withdrawn': 0,
            'skipped': 0,
            'details': []
        }

        for item in promotions:
            s_id = item.get('student_id')
            action = item.get('action', 'PROMOTE')
            target_class = item.get('target_class')
            target_section = item.get('target_section', 'A')
            roll_number = item.get('roll_number')

            try:
                student = Student.objects.get(id=s_id)
            except Student.DoesNotExist:
                results['skipped'] += 1
                continue

            if action == 'PROMOTE':
                enrollment, _ = ClassEnrollment.objects.update_or_create(
                    student=student,
                    academic_year=target_academic_year,
                    defaults={
                        'class_name': str(target_class).strip(),
                        'section_name': str(target_section).strip(),
                        'roll_number': roll_number
                    }
                )
                student.status = StudentStatus.ACTIVE
                student.save()
                results['promoted'] += 1
                results['details'].append({'student_id': s_id, 'action': 'PROMOTE', 'class': target_class})

            elif action == 'DETAIN':
                enrollment, _ = ClassEnrollment.objects.update_or_create(
                    student=student,
                    academic_year=target_academic_year,
                    defaults={
                        'class_name': str(target_class).strip(),
                        'section_name': str(target_section).strip(),
                        'roll_number': roll_number
                    }
                )
                student.status = StudentStatus.ACTIVE
                student.save()
                results['detained'] += 1
                results['details'].append({'student_id': s_id, 'action': 'DETAIN', 'class': target_class})

            elif action == 'PASSED_OUT':
                student.status = StudentStatus.PASSED_OUT
                student.save()
                results['passed_out'] += 1
                results['details'].append({'student_id': s_id, 'action': 'PASSED_OUT'})

            elif action == 'WITHDRAWN':
                student.status = StudentStatus.WITHDRAWN
                student.save()
                results['withdrawn'] += 1
                results['details'].append({'student_id': s_id, 'action': 'WITHDRAWN'})

        AuditService.log(
            action=AuditAction.PROMOTION,
            module='students',
            entity_type='ClassPromotionBatch',
            entity_id=f"{source_academic_year.name}->{target_academic_year.name}",
            actor=user,
            reason=(
                f"Year-end promotion executed: {results['promoted']} promoted, "
                f"{results['detained']} detained, {results['passed_out']} passed out, "
                f"{results['withdrawn']} withdrawn."
            ),
            changes=results,
            request=request
        )
        return results

    @classmethod
    def link_siblings(cls, student_id_1, student_id_2, user=None, request=None):
        if student_id_1 == student_id_2:
            raise ValidationError("Cannot link a student to themselves as a sibling.")

        s1 = Student.objects.get(id=student_id_1)
        s2 = Student.objects.get(id=student_id_2)

        s1.siblings.add(s2)
        s2.siblings.add(s1)

        AuditService.log(
            action=AuditAction.UPDATE,
            module='students',
            entity_type='SiblingLink',
            entity_id=f"{s1.admission_number}-{s2.admission_number}",
            actor=user,
            reason=f"Linked siblings: #{s1.admission_number} and #{s2.admission_number}",
            request=request
        )
        return True

    @classmethod
    def unlink_siblings(cls, student_id_1, student_id_2, user=None, request=None):
        s1 = Student.objects.get(id=student_id_1)
        s2 = Student.objects.get(id=student_id_2)

        s1.siblings.remove(s2)
        s2.siblings.remove(s1)

        AuditService.log(
            action=AuditAction.UPDATE,
            module='students',
            entity_type='SiblingLink',
            entity_id=f"{s1.admission_number}-{s2.admission_number}",
            actor=user,
            reason=f"Unlinked siblings: #{s1.admission_number} and #{s2.admission_number}",
            request=request
        )
        return True

    @classmethod
    def update_student_status(cls, student_id, new_status, reason="", user=None, request=None):
        student = Student.objects.get(id=student_id)
        if new_status not in StudentStatus.values:
            raise ValidationError(f"Invalid student status '{new_status}'.")

        old_status = student.status
        student.status = new_status
        student.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module='students',
            entity_type='StudentStatus',
            entity_id=str(student.id),
            actor=user,
            reason=f"Changed student {student.admission_number} status from {old_status} to {new_status}: {reason}",
            changes={'old_status': old_status, 'new_status': new_status, 'reason': reason},
            request=request
        )
        return student

    @classmethod
    def add_student_document(cls, student_id, doc_type, document_file, title="", user=None, request=None):
        student = Student.objects.get(id=student_id)
        doc = StudentDocument.objects.create(
            student=student,
            doc_type=str(doc_type).strip(),
            document_file=document_file,
            title=str(title or doc_type).strip()
        )
        AuditService.log(
            action=AuditAction.CREATE,
            module='students',
            entity_type='StudentDocument',
            entity_id=str(doc.id),
            actor=user,
            reason=f"Uploaded document '{doc.doc_type}' for {student.admission_number}",
            request=request
        )
        return doc
