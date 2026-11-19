"""
Permission Registry — Single source of truth for all RBAC permission definitions.

Each entry is a dict with:
  - codename:    unique dot-separated code, e.g. "students.profile.view"
  - module:      ModuleCode value (must match rbac.models.ModuleCode choices)
  - action:      ActionCode value (must match rbac.models.ActionCode choices)
  - name:        Human-readable label shown in the admin dashboard
  - description: Optional detail shown to role managers

Synchronisation: run `python manage.py sync_permissions` to push this list
into the database. The command is idempotent and safe to run repeatedly.

IMPORTANT (SRS §3.7):
  System Administrators do NOT automatically receive finance, payroll, or
  confidential-grievance permissions. Those must be explicitly granted via
  role assignment, just like every other role.
"""

PERMISSION_REGISTRY = [
    # ── Identity / User Management ───────────────────────────────────────
    {
        "codename": "identity.users.view",
        "module": "identity",
        "action": "view",
        "name": "View Users",
        "description": "View user account list and profiles.",
    },
    {
        "codename": "identity.users.add",
        "module": "identity",
        "action": "add",
        "name": "Create Users",
        "description": "Create new user accounts.",
    },
    {
        "codename": "identity.users.change",
        "module": "identity",
        "action": "change",
        "name": "Edit Users",
        "description": "Edit existing user account details.",
    },
    {
        "codename": "identity.users.delete",
        "module": "identity",
        "action": "delete",
        "name": "Deactivate Users",
        "description": "Deactivate (soft-delete) user accounts.",
    },
    # ── RBAC / Role Management ────────────────────────────────────────────
    {
        "codename": "rbac.role.view",
        "module": "rbac",
        "action": "view",
        "name": "View Roles",
        "description": "View role list and their permission sets.",
    },
    {
        "codename": "rbac.role.manage",
        "module": "rbac",
        "action": "add",
        "name": "Create Roles",
        "description": "Create new dynamic roles.",
    },
    {
        "codename": "rbac.role.change",
        "module": "rbac",
        "action": "change",
        "name": "Edit Roles",
        "description": "Edit role metadata and permission assignments.",
    },
    {
        "codename": "rbac.role.delete",
        "module": "rbac",
        "action": "delete",
        "name": "Delete Roles",
        "description": "Delete non-system roles.",
    },
    {
        "codename": "users.membership.manage",
        "module": "rbac",
        "action": "approve",
        "name": "Assign Roles to Users",
        "description": "Assign or revoke role assignments for users.",
    },
    # ── School Configuration ───────────────────────────────────────────────
    {
        "codename": "school_config.profile.view",
        "module": "school_config",
        "action": "view",
        "name": "View School Configuration",
        "description": "View academic year, school profile and settings.",
    },
    {
        "codename": "school_config.profile.change",
        "module": "school_config",
        "action": "change",
        "name": "Edit School Configuration",
        "description": "Edit school profile, academic years and global settings.",
    },
    # ── Admissions ────────────────────────────────────────────────────────
    {
        "codename": "admissions.application.view",
        "module": "admissions",
        "action": "view",
        "name": "View Admission Applications",
        "description": "Browse and view admission application details.",
    },
    {
        "codename": "admissions.application.add",
        "module": "admissions",
        "action": "add",
        "name": "Submit Admission Applications",
        "description": "Create new admission applications.",
    },
    {
        "codename": "admissions.application.change",
        "module": "admissions",
        "action": "change",
        "name": "Edit Admission Applications",
        "description": "Edit pending admission applications.",
    },
    {
        "codename": "admissions.application.approve",
        "module": "admissions",
        "action": "approve",
        "name": "Approve / Reject Admissions",
        "description": "Approve or reject admission applications and convert to student.",
    },
    {
        "codename": "admissions.seats.manage",
        "module": "admissions",
        "action": "change",
        "name": "Manage Seat Quotas",
        "description": "Configure class-wise seat quotas per academic year.",
    },
    # ── Students ──────────────────────────────────────────────────────────
    {
        "codename": "students.profile.view",
        "module": "students",
        "action": "view",
        "name": "View Student Profiles",
        "description": "View student profiles, guardians and enrollment history.",
    },
    {
        "codename": "students.profile.add",
        "module": "students",
        "action": "add",
        "name": "Create Student Profiles",
        "description": "Create new student profiles.",
    },
    {
        "codename": "students.profile.change",
        "module": "students",
        "action": "change",
        "name": "Edit Student Profiles",
        "description": "Edit student personal details, guardians and documents.",
    },
    {
        "codename": "students.promotion.approve",
        "module": "students",
        "action": "approve",
        "name": "Promote / Detain Students",
        "description": "Execute year-end class promotion or detention.",
    },
    {
        "codename": "students.tc.issue",
        "module": "students",
        "action": "approve",
        "name": "Issue Transfer Certificate",
        "description": "Issue TC and change student status to TC Issued.",
    },
    {
        "codename": "students.report.export",
        "module": "students",
        "action": "export",
        "name": "Export Student Reports",
        "description": "Download CSV / Excel exports of student data.",
    },
    # ── Academics ─────────────────────────────────────────────────────────
    {
        "codename": "academics.year.view",
        "module": "academics",
        "action": "view",
        "name": "View Academic Years",
        "description": "View academic year sessions, status, and historical terms.",
    },
    {
        "codename": "academics.year.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Academic Years",
        "description": "Create, edit, and configure academic years.",
    },
    {
        "codename": "academics.year.activate",
        "module": "academics",
        "action": "approve",
        "name": "Activate Academic Year",
        "description": "Switch and activate the current academic year across all ERP modules.",
    },
    {
        "codename": "academics.class.view",
        "module": "academics",
        "action": "view",
        "name": "View Classes",
        "description": "View school classes, codes, sequence orders, and statuses.",
    },
    {
        "codename": "academics.class.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Classes",
        "description": "Create, edit, and configure school classes and display ordering.",
    },
    {
        "codename": "academics.section.view",
        "module": "academics",
        "action": "view",
        "name": "View Sections",
        "description": "View school sections, streams, capacities, and statuses.",
    },
    {
        "codename": "academics.section.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Sections",
        "description": "Create, edit, configure, and delete class sections.",
    },
    {
        "codename": "academics.stream.view",
        "module": "academics",
        "action": "view",
        "name": "View Streams",
        "description": "View senior-secondary streams for Classes 11 and 12.",
    },
    {
        "codename": "academics.stream.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Streams",
        "description": "Configure, edit, and activate/deactivate streams for senior classes.",
    },
    {
        "codename": "academics.timetable.view",
        "module": "academics",
        "action": "view",
        "name": "View Timetable",
        "description": "View class timetables and subject assignments.",
    },
    {
        "codename": "academics.timetable.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Timetable",
        "description": "Create and edit class timetables and teacher assignments.",
    },
    {
        "codename": "academics.room.view",
        "module": "academics",
        "action": "view",
        "name": "View Rooms & Facilities",
        "description": "View school classrooms, laboratories, facilities, and capacity details.",
    },
    {
        "codename": "academics.room.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Rooms & Facilities",
        "description": "Create, edit, configure, and activate/deactivate school rooms and laboratories.",
    },
    {
        "codename": "academics.subjects.view",
        "module": "academics",
        "action": "view",
        "name": "View Subjects",
        "description": "View subject master catalog, codes, types, and components.",
    },
    {
        "codename": "academics.subjects.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Subjects",
        "description": "Create and edit subjects in the master catalog.",
    },
    {
        "codename": "academics.class_subject.view",
        "module": "academics",
        "action": "view",
        "name": "View Class-Subject Mappings",
        "description": "View curriculum subjects mapped to classes, streams, and sections.",
    },
    {
        "codename": "academics.class_subject.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Class-Subject Mappings",
        "description": "Map subjects to classes, streams, and sections, and set compulsory/elective types.",
    },
    {
        "codename": "academics.assignment.view",
        "module": "academics",
        "action": "view",
        "name": "View Teacher Assignments",
        "description": "View class teacher and subject teacher assignments and faculty workloads.",
    },
    {
        "codename": "academics.assignment.manage",
        "module": "academics",
        "action": "change",
        "name": "Manage Teacher Assignments",
        "description": "Assign, reassign, or remove class teachers and subject teachers.",
    },
    # ── Attendance ────────────────────────────────────────────────────────
    {
        "codename": "attendance.record.view",
        "module": "attendance",
        "action": "view",
        "name": "View Attendance Records",
        "description": "View daily and period-wise attendance data.",
    },
    {
        "codename": "attendance.record.mark",
        "module": "attendance",
        "action": "add",
        "name": "Mark Attendance",
        "description": "Mark daily or period-wise attendance for a section.",
    },
    {
        "codename": "attendance.correction.approve",
        "module": "attendance",
        "action": "approve",
        "name": "Approve Attendance Corrections",
        "description": "Approve or reject attendance correction requests.",
    },
    {
        "codename": "attendance.report.export",
        "module": "attendance",
        "action": "export",
        "name": "Export Attendance Reports",
        "description": "Download attendance shortage and summary reports.",
    },
    # ── Finance ───────────────────────────────────────────────────────────
    {
        "codename": "finance.fee_structure.view",
        "module": "finance",
        "action": "view",
        "name": "View Fee Structures",
        "description": "View fee heads and class-wise fee structures.",
    },
    {
        "codename": "finance.fee_structure.manage",
        "module": "finance",
        "action": "change",
        "name": "Manage Fee Structures",
        "description": "Create and edit fee heads and class-wise structures.",
    },
    {
        "codename": "finance.invoice.view",
        "module": "finance",
        "action": "view",
        "name": "View Invoices",
        "description": "View student invoices and outstanding dues.",
    },
    {
        "codename": "finance.payment.record",
        "module": "finance",
        "action": "add",
        "name": "Record Payments",
        "description": "Record offline fee payments and generate receipts.",
    },
    {
        "codename": "finance.concession.approve",
        "module": "finance",
        "action": "approve",
        "name": "Approve Concessions",
        "description": "Approve or reject fee concession requests.",
    },
    {
        "codename": "finance.refund.approve",
        "module": "finance",
        "action": "approve",
        "name": "Approve Refunds",
        "description": "Approve or reject fee refund requests.",
    },
    {
        "codename": "finance.report.export",
        "module": "finance",
        "action": "export",
        "name": "Export Finance Reports",
        "description": "Download fee collection, dues, and defaulter reports.",
    },
    # ── Examinations ──────────────────────────────────────────────────────
    {
        "codename": "exams.marks.view",
        "module": "examinations",
        "action": "view",
        "name": "View Marks",
        "description": "View marks entry sheets and exam results.",
    },
    {
        "codename": "exams.marks.enter",
        "module": "examinations",
        "action": "add",
        "name": "Enter Marks",
        "description": "Enter or edit marks for assigned subjects.",
    },
    {
        "codename": "exams.results.approve",
        "module": "examinations",
        "action": "approve",
        "name": "Approve Exam Results",
        "description": "Approve calculated results before publication.",
    },
    {
        "codename": "exams.results.publish",
        "module": "examinations",
        "action": "approve",
        "name": "Publish Exam Results",
        "description": "Publish approved results and enable report card generation.",
    },
    # ── HRMS & Payroll ────────────────────────────────────────────────────
    {
        "codename": "hrms.staff.view",
        "module": "hrms",
        "action": "view",
        "name": "View Staff Profiles",
        "description": "View staff profiles, designations and departments.",
    },
    {
        "codename": "hrms.staff.manage",
        "module": "hrms",
        "action": "change",
        "name": "Manage Staff Profiles",
        "description": "Create and edit staff profiles.",
    },
    {
        "codename": "hrms.leave.approve",
        "module": "hrms",
        "action": "approve",
        "name": "Approve Leave Requests",
        "description": "Approve or reject staff leave requests.",
    },
    {
        "codename": "hrms.payroll.view",
        "module": "hrms",
        "action": "view",
        "name": "View Payroll",
        "description": "View payroll runs and payslips. SRS: NOT auto-granted to System Administrator.",
    },
    {
        "codename": "hrms.payroll.run",
        "module": "hrms",
        "action": "approve",
        "name": "Run Payroll",
        "description": "Execute payroll runs and generate payslips. SRS: NOT auto-granted to System Administrator.",
    },
    # ── Library ───────────────────────────────────────────────────────────
    {
        "codename": "library.catalog.view",
        "module": "library",
        "action": "view",
        "name": "View Library Catalog",
        "description": "Browse book catalog, copies and member records.",
    },
    {
        "codename": "library.circulation.manage",
        "module": "library",
        "action": "change",
        "name": "Manage Circulation",
        "description": "Issue, return, renew and reserve library books.",
    },
    {
        "codename": "library.catalog.manage",
        "module": "library",
        "action": "add",
        "name": "Manage Library Catalog",
        "description": "Add and edit book catalog entries and copies.",
    },
    # ── Transport ─────────────────────────────────────────────────────────
    {
        "codename": "transport.routes.view",
        "module": "transport",
        "action": "view",
        "name": "View Transport Routes",
        "description": "View vehicles, routes, stops and student allocations.",
    },
    {
        "codename": "transport.routes.manage",
        "module": "transport",
        "action": "change",
        "name": "Manage Transport",
        "description": "Manage vehicles, routes, stops and student transport allocations.",
    },
    # ── Inventory ─────────────────────────────────────────────────────────
    {
        "codename": "inventory.assets.view",
        "module": "inventory",
        "action": "view",
        "name": "View Inventory",
        "description": "View assets, consumables, vendors and stock levels.",
    },
    {
        "codename": "inventory.assets.manage",
        "module": "inventory",
        "action": "change",
        "name": "Manage Inventory",
        "description": "Create purchase orders and record stock movements.",
    },
    # ── Helpdesk ──────────────────────────────────────────────────────────
    {
        "codename": "helpdesk.ticket.view",
        "module": "helpdesk",
        "action": "view",
        "name": "View Helpdesk Tickets",
        "description": "View support tickets assigned to your team or role.",
    },
    {
        "codename": "helpdesk.ticket.manage",
        "module": "helpdesk",
        "action": "change",
        "name": "Manage Helpdesk Tickets",
        "description": "Assign, comment on, escalate and resolve tickets.",
    },
    {
        "codename": "helpdesk.grievance.view",
        "module": "helpdesk",
        "action": "view",
        "name": "View Confidential Grievances",
        "description": "View confidential grievance tickets. SRS: restricted access only.",
    },
    # ── Analytics & Reports ────────────────────────────────────────────────
    {
        "codename": "analytics.reports.view",
        "module": "analytics",
        "action": "view",
        "name": "View Reports & Analytics",
        "description": "Access operational dashboards and summary reports.",
    },
    {
        "codename": "analytics.reports.export",
        "module": "analytics",
        "action": "export",
        "name": "Export Analytics Reports",
        "description": "Download CSV/Excel exports of operational reports.",
    },
    # ── Audit Logs ────────────────────────────────────────────────────────
    {
        "codename": "audit.logs.view",
        "module": "audit",
        "action": "view",
        "name": "View Audit Logs",
        "description": "Read immutable audit trail for sensitive operations.",
    },
]

