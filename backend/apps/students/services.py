import uuid
from django.db import transaction
from django.utils import timezone
from apps.students.models import Student, StudentStatus, ClassEnrollment, TransferCertificate
from apps.audit.services import AuditService
from apps.audit.models import AuditAction
from common.exceptions import SchoolERPException


class StudentService:
    """
    Business logic for student management, promotions, and TC issuance.
    """

    @classmethod
    @transaction.atomic
    def issue_transfer_certificate(cls, student_id, reason, user, dues_cleared=True):
        student = Student.objects.select_for_update().get(id=student_id)
        if hasattr(student, 'transfer_certificate'):
            raise SchoolERPException("Transfer Certificate has already been issued for this student.")

        tc_num = f"TC-{timezone.now().year}-{uuid.uuid4().hex[:6].upper()}"
        tc = TransferCertificate.objects.create(
            tc_number=tc_num,
            student=student,
            issue_date=timezone.now().date(),
            reason_for_leaving=reason,
            dues_cleared=dues_cleared
        )

        student.status = StudentStatus.TC_ISSUED
        student.save()

        AuditService.log(
            action=AuditAction.TC_ISSUE,
            module='students',
            entity_type='TransferCertificate',
            entity_id=tc.id,
            actor=user,
            reason=f"TC issued: {reason}"
        )
        return tc

    @classmethod
    @transaction.atomic
    def promote_students(cls, target_academic_year, target_class, target_section, student_ids, user):
        """
        Promote a list of students to the next class and academic year.
        """
        promoted = []
        for student_id in student_ids:
            student = Student.objects.get(id=student_id, status=StudentStatus.ACTIVE)
            enrollment, created = ClassEnrollment.objects.update_or_create(
                student=student,
                academic_year=target_academic_year,
                defaults={
                    'class_name': target_class,
                    'section_name': target_section
                }
            )
            promoted.append(enrollment)

        AuditService.log(
            action=AuditAction.PROMOTION,
            module='students',
            entity_type='ClassEnrollment',
            entity_id=f"{target_class}-{target_section}",
            actor=user,
            reason=f"Promoted {len(promoted)} students to {target_class} {target_section} ({target_academic_year.name})"
        )
        return promoted
