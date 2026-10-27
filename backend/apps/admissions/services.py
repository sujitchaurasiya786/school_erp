import uuid
from django.db import transaction
from django.utils import timezone
from apps.admissions.models import AdmissionApplication, ApplicationStatus
from apps.audit.services import AuditService
from apps.audit.models import AuditAction
from common.exceptions import SchoolERPException


class AdmissionService:
    """
    Handles admission review, approval, and transactional conversion to student.
    """

    @classmethod
    def approve_application(cls, application, user, remarks=''):
        application.status = ApplicationStatus.APPROVED
        application.interview_remarks = remarks
        application.save()

        AuditService.log(
            action=AuditAction.APPROVE,
            module='admissions',
            entity_type='AdmissionApplication',
            entity_id=application.id,
            actor=user,
            reason=remarks
        )
        return application

    @classmethod
    def reject_application(cls, application, user, reason):
        if not reason:
            raise SchoolERPException("Rejection reason is required.")

        application.status = ApplicationStatus.REJECTED
        application.rejection_reason = reason
        application.save()

        AuditService.log(
            action=AuditAction.REJECT,
            module='admissions',
            entity_type='AdmissionApplication',
            entity_id=application.id,
            actor=user,
            reason=reason
        )
        return application
