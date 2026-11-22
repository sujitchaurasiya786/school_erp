import uuid
from datetime import date
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.admissions.models import (
    AdmissionApplication, ApplicationDocument, ApplicationStatus,
    DocumentStatus, SeatQuota
)
from apps.audit.models import AuditAction
from apps.audit.services import AuditService
from apps.students.models import Student, StudentStatus, Guardian, ClassEnrollment
from apps.school_config.models import AcademicYear
from common.exceptions import BusinessConflictError, ResourceNotFoundError, SchoolERPException


class AdmissionService:
    """
    Handles complete admission lifecycle:
      - Application intake and validation
      - Quota and waitlist management
      - Document verification
      - Entrance test and interview evaluation
      - Approval and rejection workflows
      - Transaction-safe conversion to Student with guardian/sibling deduplication
    """

    @classmethod
    def generate_application_number(cls, academic_year):
        ay_prefix = str(academic_year.start_date.year) if academic_year and getattr(academic_year, 'start_date', None) else timezone.now().strftime('%Y')
        count = AdmissionApplication.objects.filter(application_number__startswith=f"APP-{ay_prefix}-").count() + 1
        candidate = f"APP-{ay_prefix}-{count:04d}"
        while AdmissionApplication.objects.filter(application_number=candidate).exists():
            count += 1
            candidate = f"APP-{ay_prefix}-{count:04d}"
        return candidate

    @classmethod
    def generate_admission_number(cls, academic_year):
        ay_prefix = str(academic_year.start_date.year) if academic_year and getattr(academic_year, 'start_date', None) else timezone.now().strftime('%Y')
        count = Student.objects.filter(admission_number__startswith=f"ADM-{ay_prefix}-").count() + 1
        candidate = f"ADM-{ay_prefix}-{count:04d}"
        while Student.objects.filter(admission_number=candidate).exists():
            count += 1
            candidate = f"ADM-{ay_prefix}-{count:04d}"
        return candidate

    @classmethod
    @transaction.atomic
    def create_application(cls, academic_year, applying_class, first_name, last_name,
                           date_of_birth, gender, father_name, mother_name='',
                           guardian_phone='', address='', guardian_email='', previous_school='',
                           actor=None, request=None, **kwargs):
        ay = AcademicYear.objects.get(pk=academic_year) if not isinstance(academic_year, AcademicYear) else academic_year
        app_num = cls.generate_application_number(ay)

        app = AdmissionApplication(
            application_number=app_num,
            academic_year=ay,
            applying_class=str(applying_class).strip(),
            first_name=str(first_name).strip(),
            last_name=str(last_name).strip(),
            date_of_birth=date_of_birth,
            gender=gender,
            father_name=str(father_name).strip(),
            mother_name=str(mother_name).strip(),
            guardian_phone=str(guardian_phone).strip(),
            guardian_email=str(guardian_email or '').strip(),
            address=str(address).strip(),
            previous_school=str(previous_school or '').strip(),
            status=ApplicationStatus.SUBMITTED
        )
        app.clean()
        app.save()

        AuditService.log(
            action=AuditAction.CREATE,
            module='admissions',
            entity_type='AdmissionApplication',
            entity_id=str(app.pk),
            actor=actor,
            reason=f"Created application {app.application_number} for {app.first_name} {app.last_name}",
            request=request
        )
        return app

    @classmethod
    def approve_application(cls, application, user, remarks='', check_seats=True, request=None):
        if application.status == ApplicationStatus.ADMITTED:
            raise BusinessConflictError("Application has already been admitted as a student.")

        if check_seats:
            quota = SeatQuota.objects.filter(
                academic_year=application.academic_year,
                class_name=application.applying_class
            ).first()
            if quota:
                admitted_approved_count = AdmissionApplication.objects.filter(
                    academic_year=application.academic_year,
                    applying_class=application.applying_class,
                    status__in=[ApplicationStatus.APPROVED, ApplicationStatus.ADMITTED]
                ).exclude(pk=application.pk).count()

                waitlisted_count = AdmissionApplication.objects.filter(
                    academic_year=application.academic_year,
                    applying_class=application.applying_class,
                    status=ApplicationStatus.WAITLIST
                ).exclude(pk=application.pk).count()

                if admitted_approved_count >= quota.total_seats:
                    if waitlisted_count >= quota.waitlist_limit:
                        raise BusinessConflictError(
                            f"Cannot approve application. Class '{application.applying_class}' has reached maximum total seats ({quota.total_seats}) and waitlist capacity ({quota.waitlist_limit})."
                        )
                    application.status = ApplicationStatus.WAITLIST
                    application.interview_remarks = remarks or "Seat capacity reached; placed on waitlist."
                    application.save()
                    AuditService.log(
                        action=AuditAction.APPROVE,
                        module='admissions',
                        entity_type='AdmissionApplication',
                        entity_id=str(application.pk),
                        actor=user,
                        reason=f"Application placed on waitlist (capacity reached: {admitted_approved_count}/{quota.total_seats}, waitlisted: {waitlisted_count}/{quota.waitlist_limit})",
                        request=request
                    )
                    return application

        application.status = ApplicationStatus.APPROVED
        application.interview_remarks = remarks
        application.save()

        AuditService.log(
            action=AuditAction.APPROVE,
            module='admissions',
            entity_type='AdmissionApplication',
            entity_id=str(application.pk),
            actor=user,
            reason=remarks or "Application approved",
            request=request
        )
        return application

    @classmethod
    def reject_application(cls, application, user, reason, request=None):
        if not str(reason or '').strip():
            raise ValidationError({'reason': _("Rejection reason is required.")})

        if application.status == ApplicationStatus.ADMITTED:
            raise BusinessConflictError("Cannot reject an application that has already been admitted.")

        application.status = ApplicationStatus.REJECTED
        application.rejection_reason = str(reason).strip()
        application.save()

        AuditService.log(
            action=AuditAction.REJECT,
            module='admissions',
            entity_type='AdmissionApplication',
            entity_id=str(application.pk),
            actor=user,
            reason=reason,
            request=request
        )
        return application

    @classmethod
    def verify_document(cls, document_id, verification_status, notes="", user=None, request=None):
        try:
            doc = ApplicationDocument.objects.select_related('application').get(pk=document_id)
        except ApplicationDocument.DoesNotExist:
            raise ResourceNotFoundError(f"Application document with ID {document_id} not found.")

        if verification_status not in DocumentStatus.values:
            raise ValidationError({'verification_status': _(f"Invalid verification status '{verification_status}'.")})

        doc.verification_status = verification_status
        doc.verification_notes = str(notes or '').strip()
        doc.save()

        app = doc.application
        docs = app.documents.all()
        if docs.exists() and all(d.verification_status == DocumentStatus.VERIFIED for d in docs):
            if app.status in [ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW]:
                app.status = ApplicationStatus.DOCUMENTS_VERIFIED
                app.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module='admissions',
            entity_type='ApplicationDocument',
            entity_id=str(doc.pk),
            actor=user,
            reason=f"Verified document '{doc.doc_type}' as {doc.verification_status}",
            changes={"status": doc.verification_status, "notes": doc.verification_notes},
            request=request
        )
        return doc

    @classmethod
    def record_evaluation(cls, application_id, test_score=None, interview_remarks="", status=None, user=None, request=None):
        try:
            app = AdmissionApplication.objects.get(pk=application_id)
        except AdmissionApplication.DoesNotExist:
            raise ResourceNotFoundError(f"Admission application with ID {application_id} not found.")

        if test_score is not None:
            app.test_score = test_score
        if interview_remarks:
            app.interview_remarks = str(interview_remarks).strip()
        if status and status in ApplicationStatus.values:
            app.status = status

        app.save()

        AuditService.log(
            action=AuditAction.UPDATE,
            module='admissions',
            entity_type='AdmissionApplication',
            entity_id=str(app.pk),
            actor=user,
            reason=f"Recorded evaluation for application {app.application_number}",
            changes={"test_score": str(app.test_score), "status": app.status},
            request=request
        )
        return app

    @classmethod
    @transaction.atomic
    def convert_to_student(cls, application_id, section_name="A", roll_number=None, admission_date=None, user=None, request=None):
        """
        Converts an approved applicant into a student without duplicating identity or guardian data.
        Executes inside an atomic database transaction.
        """
        try:
            app = AdmissionApplication.objects.select_for_update().select_related('academic_year').get(pk=application_id)
        except AdmissionApplication.DoesNotExist:
            raise ResourceNotFoundError(f"Admission application with ID {application_id} not found.")

        if app.status == ApplicationStatus.ADMITTED:
            raise BusinessConflictError(f"Application '{app.application_number}' has already been converted to student '{app.converted_student_id}'.")

        if app.status != ApplicationStatus.APPROVED:
            raise ValidationError(
                f"Cannot convert application with status '{app.get_status_display()}'. Only APPROVED applications can be converted to students."
            )

        # 1. Deduplicate / reuse Guardian by phone number
        phone = app.guardian_phone.strip()
        guardian = Guardian.objects.filter(phone=phone).first()

        if not guardian:
            father_parts = app.father_name.strip().split()
            f_name = father_parts[0] if father_parts else "Guardian"
            l_name = " ".join(father_parts[1:]) if len(father_parts) > 1 else (app.last_name or "")
            guardian = Guardian.objects.create(
                first_name=f_name,
                last_name=l_name,
                relationship="Father",
                phone=phone,
                email=app.guardian_email or ""
            )

        # 2. Generate unique admission number
        adm_num = cls.generate_admission_number(app.academic_year)
        adm_date = admission_date or timezone.now().date()

        # 3. Create Student record
        student = Student.objects.create(
            admission_number=adm_num,
            first_name=app.first_name,
            last_name=app.last_name,
            date_of_birth=app.date_of_birth,
            gender=app.gender,
            address=app.address,
            previous_school=app.previous_school,
            admission_date=adm_date,
            primary_guardian=guardian,
            status=StudentStatus.ACTIVE
        )

        # 4. Link existing siblings with the same primary guardian
        existing_siblings = Student.objects.filter(
            primary_guardian=guardian
        ).exclude(pk=student.pk)
        for sib in existing_siblings:
            student.siblings.add(sib)
            sib.siblings.add(student)

        # 5. Create initial ClassEnrollment
        ClassEnrollment.objects.create(
            student=student,
            academic_year=app.academic_year,
            class_name=app.applying_class,
            section_name=str(section_name or 'A').strip(),
            roll_number=roll_number
        )

        # 6. Update application status
        app.status = ApplicationStatus.ADMITTED
        app.converted_student_id = student.admission_number
        app.save()

        # 7. Audit log
        AuditService.log(
            action=AuditAction.CREATE,
            module='admissions',
            entity_type='StudentConversion',
            entity_id=str(student.pk),
            actor=user,
            reason=f"Converted application {app.application_number} to student {student.admission_number}",
            changes={
                "application_number": app.application_number,
                "admission_number": student.admission_number,
                "class_name": app.applying_class,
                "section_name": section_name,
                "guardian": str(guardian)
            },
            request=request
        )

        return student
