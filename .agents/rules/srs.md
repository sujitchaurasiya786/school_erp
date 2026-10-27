---
trigger: always_on
---

# School ERP System — Software Requirements Specification & Technical Design

Single CBSE School (Nursery to Class 12) • Local Development First • 12 Active Modules

**Technology baseline:** Python, Django, Django REST Framework, MySQL, Django Templates (HTML5, CSS3, JavaScript), Bootstrap + regular CSS, Git. Execution: local Windows computer first; deployment later.

## Document control

| Item | Decision |
| --- | --- |
| Project type | School ERP for one CBSE school, classes Nursery to 12 |
| Environment | Local development on Windows; no cloud deployment in the current phase |
| Frontend | Django Templates with HTML5, CSS3, JavaScript, Bootstrap and regular CSS. No React or separate frontend framework |
| Backend | Python + Django + Django REST Framework |
| Database | MySQL (InnoDB, utf8mb4, strict mode) used consistently |
| Authentication | Django session authentication (same-origin) |
| Architecture | Modular monolith with REST API under /api/v1/ |
| Active modules | 12 (listed in section 2.2) |
| Deferred modules | Hostel & Mess, Alumni, LMS, Communications & Notifications, AI Assistant, Campus Operations & Security |
| Not applicable to a school | Training & Placement, counselling/merit/seat allocation, semesters/credits/curricula versions, accreditation-style compliance |
| Role management | Fully dynamic: roles are created and edited by Admin/Principal from the dashboard |

## 1. Executive summary

The School ERP System manages the main academic and administrative workflows of one CBSE school from Nursery to Class 12. The first release is developed and run locally. It uses a Django-template frontend, a Django REST Framework API and a MySQL database.

The system is a modular monolith: one Django project, one database, shared authentication/authorization and a separate Django app for each business domain. This is easier to build and debug locally than microservices and can be deployed later without redesign.

A key design requirement is that **roles are dynamic**. Admin or Principal can create a new role from the dashboard, choose its permissions, scope it and assign it to users without any code change.

## 2. Goals and scope

### 2.1 Goals

- Keep student, guardian, staff, class and academic-year records consistent.
- Connect admission, classes, attendance, fees and exams through shared identifiers and workflows.
- Provide role-specific screens for admin, principal, teachers, accountants, parents and students.
- Protect sensitive student, finance, exam and payroll data with server-side permissions and audit trails.
- Make roles and permissions manageable from the dashboard, not hardcoded.
- Keep deferred modules separable so they can be added later without rewriting the core.
- Build and test locally before planning production deployment.

### 2.2 In-scope modules

| No. | Module | Django app | Initial scope |
| --- | --- | --- | --- |
| 1 | Admission | admissions | Applications, class-wise seats, document verification, optional entrance/interview, approval, conversion to student |
| 2 | Students | students | Student profile, admission number, roll number, guardians, siblings, documents, class history, promotion, TC |
| 3 | Academics | academics | Academic year, classes, sections, streams (11-12), subjects, class-subject mapping, teacher assignment, rooms, timetable |
| 4 | Attendance | attendance | Daily attendance, optional period-wise attendance, holidays, corrections, shortage reports |
| 5 | Fees | finance | Fee heads, class-wise structures, invoices, offline payments, receipts, concessions, late fines, refunds, dues |
| 6 | Exams | examinations | Exam terms, assessments, marks entry, grading, approval, publication, report cards |
| 7 | HRMS & Payroll | hrms | Staff profiles, designations, leave, staff attendance, payroll runs, payslips |
| 8 | Library | library | Catalog, copies, members, issue/return/renew, reservations, fines |
| 9 | Transport | transport | Vehicles, drivers, routes, stops, student allocation, maintenance, permit reminders |
| 10 | Inventory | inventory | Assets, consumables, vendors, purchase orders, stock movements, maintenance |
| 11 | Helpdesk | helpdesk | Complaints/tickets from parents, students and staff, assignment, escalation, resolution |
| 12 | Reports & Analytics | analytics | Operational reports, dashboards, filters and exports |

Shared apps: identity (users), rbac (roles and permissions), school\_config (school settings), audit (audit log) and common (utilities).

### 2.3 Deferred — not implemented now

| Module | Note |
| --- | --- |
| Hostel & Mess | Skipped because the school has no hostel. Can be added later as a new app. |
| Alumni | Not required now. |
| Communications & Notifications | SMS/WhatsApp/email to parents will be a future module. Current modules must not hardcode any provider. |
| LMS | Future; will link to class-section-subject records. |
| AI Assistant | Future; permission-aware. |
| Campus Operations & Security | Visitor logs, gate passes, incidents. Future. |

Deferred means planned but not built. Do not create empty screens or placeholder apps.

## 3. Functional requirements by module

### 3.1 Admission

- Create and track applications with unique application numbers, applying-for class and academic year.
- Configure seats per class and academic year; support a waitlist.
- Upload and verify documents (birth certificate, previous school report/TC, photos, address proof as configured) with status and notes.
- Optional entrance test or interview record with result.
- Approve or reject an application with a reason.
- Convert an approved applicant into a student without duplicating identity or guardian data, inside one database transaction.

### 3.2 Students

- Maintain student profile: unique admission number, roll number per section and year, date of birth, gender, contact and address.
- Guardian records (father, mother, other guardian) with phone, email and relationship.
- Link siblings so concessions and parent login can be shared.
- Previous school details and student documents.
- Status: active, TC issued, passed out, withdrawn, suspended.
- Class history: one enrollment per student per academic year (class, section, roll number).
- Year-end promotion: promote a whole class or selected students to the next class, with detain/exception handling.
- Issue Transfer Certificate and bonafide certificate.
- Restrict personal data to authorized roles; a student or verified parent sees only their own data.

### 3.3 Academics

- Academic year (for example April to March) with one active year.
- Classes Nursery to 12, sections, and streams (Science, Commerce, Arts) for classes 11-12.
- Subjects and class-subject mapping, including compulsory, optional and elective subjects.
- Class-teacher assignment per section and subject-teacher assignment per class-section-subject.
- Rooms, periods and timetable slots.
- Prevent conflicts: a teacher or room cannot be in two places in one period.

### 3.4 Attendance

- Daily attendance per section marked by the class teacher; optional period-wise attendance per subject.
- School calendar with holidays and working days.
- One attendance record per student per day (or per period); duplicates are rejected.
- Corrections only through a reasoned, authorized request and approval workflow.
- Attendance percentage reports and configurable shortage threshold (default 75%).

### 3.5 Fees

- Fee heads (tuition, admission, annual, exam, transport, etc.) and class-wise fee structures per academic year.
- Fee frequency: monthly, quarterly, half-yearly or annual.
- Generate invoices per student; record offline payments (cash, cheque, UPI reference) with receipts.
- Concessions (sibling, staff child, RTE, merit, other) with approval.
- Configurable late fine; outstanding dues and defaulter list.
- Refund requests with approval.
- Keep a full transaction history; confirmed payments are never silently deleted, only reversed through a recorded entry.
- Transport fee can be linked to the transport allocation.

### 3.6 Exams

- Exam terms and types: unit tests, half-yearly, annual, internal assessment, practical.
- Assessments per class and subject with maximum marks and weightage (for example theory plus internal/practical components for classes 9-12).
- Marks entry with validation (0 to maximum; absent/exempt markers).
- Configurable grading rules per class band (for example Nursery-8, 9-10, 11-12).
- Result calculation, then approval, then publication as separate steps with separate permissions.
- Report card generation (printable); published results remain stable if configuration changes later.
- Audit trail for every mark correction.

### 3.7 HRMS & Payroll

- Staff profiles, designation, department/section, employment status; teaching and non-teaching flags.
- Leave types, leave requests and approvals; staff attendance.
- Payroll runs and payslips with configurable salary components.
- Salary data visible only to authorized roles; System Administrator has no automatic access.

### 3.8 Library

- Book catalog and individual copy/accession records.
- Members: students and staff.
- Issue, return, renew and reserve; due dates and overdue tracking; configurable fines.
- A physical copy cannot be issued to two borrowers at once.

### 3.9 Transport

- Vehicles, drivers, routes and stops.
- Assign students to routes and stops; link transport fee status.
- Vehicle maintenance records; reminders for permit, fitness, insurance and driver license expiry.
- GPS tracking is not included in the first version.

### 3.10 Inventory

- Asset register and consumables (stationery, lab items, sports items, furniture).
- Vendors and purchase orders.
- Stock movements with quantity, source, destination and actor; stock totals change only through recorded movements.
- Asset allocation, maintenance records and low-stock alerts.

### 3.11 Helpdesk

- Tickets with category, priority, status and responsible team or person.
- Parents, students and staff raise tickets and see only their own.
- Comments, assignment history, deadlines, escalation and resolution tracking.
- Confidential grievances visible only to authorized users.

### 3.12 Reports & Analytics

- Admission, student strength (class/section-wise), attendance, fee collection and dues, exam results, payroll summary, library and transport reports.
- Filters by academic year, class, section and date range where applicable.
- Authorized CSV/Excel exports; report totals must reconcile with source records.

## 4. Non-functional requirements

- **Maintainable:** each domain is its own Django app with clear service boundaries; business rules live in services, not in views or templates.
- **Secure:** permissions enforced on the backend for every request; input validated; secrets kept in an ignored .env; uploaded documents stored privately and served only through permission-checked views.
- **Reliable:** database transactions for admission conversion, promotion, payment allocation, result publication and stock movements.
- **Auditable:** sensitive changes to students, finance, marks, roles/permissions and payroll are logged.
- **Usable:** responsive layout for desktop and mobile; clear validation messages, empty states and loading states.
- **Testable:** model, service, API, permission and workflow tests.
- **Portable:** environment-based configuration so local setup can move to a server later.
- **Accessible:** semantic HTML, labels, keyboard navigation, readable contrast.

## 5. Technical architecture

### 5.1 Request flow
Browser (Django Templates + Bootstrap/CSS + JavaScript) → Django views and Django REST Framework API (/api/v1/) → authentication and dynamic permission checks → module services and validation → audit logging → MySQL. Uploaded files go to private local media storage during development.

Straightforward screens use normal Django form submissions. JavaScript calls the API only where it clearly helps (live search, inline actions, dynamic dropdowns)