"""
Web authentication views for School ERP.
Handles session-based login, logout, and mandatory password change screens.
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.identity.models import User
from apps.identity.services import IdentityService
from apps.school_config.models import SchoolProfile, AcademicYear
from apps.audit.models import AuditAction
from apps.audit.services import AuditService


@ensure_csrf_cookie
def login_view(request):
    """
    Renders the web login page and processes session-based authentication.
    Supports login via username or email.
    """
    # If already logged in, redirect to password-change or home dashboard
    if request.user.is_authenticated:
        if getattr(request.user, 'must_change_password', False):
            return redirect('password-change')
        return redirect('home-dashboard')

    next_url = request.GET.get('next') or request.POST.get('next') or 'home-dashboard'
    school_profile = SchoolProfile.objects.first()
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        remember_me = request.POST.get('remember_me')

        if not identifier or not password:
            messages.error(request, 'Please enter both username/email and password.')
            return render(request, 'auth/login.html', {
                'school_profile': school_profile,
                'current_year': current_year,
                'username': identifier,
                'next': next_url,
            })

        from django.db.models import Q

        # 1. Look up candidate user by username or email (case-insensitive)
        candidate = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
        ).first()

        auth_username = candidate.username if candidate else identifier
        user = authenticate(request, username=auth_username, password=password)

        # 2. Handle plain-text password from manual database insertion or candidate password match
        if user is None and candidate:
            if candidate.password == password:
                # Password was saved as plain text; hash it properly and re-authenticate
                candidate.set_password(password)
                candidate.save(update_fields=['password'])
                user = authenticate(request, username=candidate.username, password=password)
            elif candidate.check_password(password):
                user = candidate

        if user is None:
            if candidate is None:
                messages.error(
                    request,
                    f"Invalid credentials. User account '{identifier}' was not found. Please check your username or email."
                )
            else:
                messages.error(
                    request,
                    "Invalid credentials. Incorrect password. Please verify your password and try again."
                )
            return render(request, 'auth/login.html', {
                'school_profile': school_profile,
                'current_year': current_year,
                'username': identifier,
                'next': next_url,
            })

        if not user.is_active:
            messages.error(request, 'Your account has been deactivated. Please contact school administration.')
            return render(request, 'auth/login.html', {
                'school_profile': school_profile,
                'current_year': current_year,
                'username': identifier,
                'next': next_url,
            })

        # Log user into the session
        login(request, user)

        # Set session expiry: 2 weeks for remember me, otherwise browser close
        if remember_me:
            request.session.set_expiry(1209600)  # 14 days
        else:
            request.session.set_expiry(0)

        # Record audit log entry
        AuditService.log(
            action=AuditAction.LOGIN,
            module='identity',
            entity_type='User',
            entity_id=str(user.pk),
            actor=user,
            request=request,
            reason='Successful web session login'
        )

        # Check mandatory first login password change requirement
        if getattr(user, 'must_change_password', False):
            messages.info(
                request,
                f"Welcome, {user.full_name}! For security, you must set a new password before accessing the system."
            )
            return redirect('password-change')

        messages.success(request, f'Welcome back, {user.full_name}!')

        # Validate redirect target to prevent open redirect vulnerabilities
        if url_has_allowed_host_and_scheme(url=next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect('home-dashboard')

    return render(request, 'auth/login.html', {
        'school_profile': school_profile,
        'current_year': current_year,
        'next': next_url,
    })


@login_required(login_url='login')
def password_change_view(request):
    """
    Mandatory password change view on first login.
    User must provide and confirm a new valid password before accessing any ERP module.
    """
    # If the user does not need to change password, redirect to home dashboard
    if not getattr(request.user, 'must_change_password', False):
        return redirect('home-dashboard')

    school_profile = SchoolProfile.objects.first()
    current_year = AcademicYear.objects.filter(is_current=True).first()

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not new_password or not confirm_password:
            messages.error(request, 'Both New Password and Confirm Password fields are mandatory.')
            return render(request, 'auth/password_change.html', {
                'school_profile': school_profile,
                'current_year': current_year,
            })

        if new_password != confirm_password:
            messages.error(request, 'New password and confirmation password do not match.')
            return render(request, 'auth/password_change.html', {
                'school_profile': school_profile,
                'current_year': current_year,
            })

        try:
            IdentityService.change_password(
                user=request.user,
                new_password=new_password,
                confirm_password=confirm_password,
                actor=request.user,
                request=request,
            )
        except ValidationError as err:
            if hasattr(err, 'message_dict'):
                for field_errors in err.message_dict.values():
                    for err_msg in field_errors:
                        messages.error(request, err_msg)
            elif hasattr(err, 'messages'):
                for err_msg in err.messages:
                    messages.error(request, err_msg)
            else:
                messages.error(request, str(err))

            return render(request, 'auth/password_change.html', {
                'school_profile': school_profile,
                'current_year': current_year,
            })

        messages.success(request, 'Password changed successfully! You now have full access to your ERP dashboard.')
        return redirect('home-dashboard')

    return render(request, 'auth/password_change.html', {
        'school_profile': school_profile,
        'current_year': current_year,
    })


def logout_view(request):
    """
    Terminates the user's web session, records an audit log, and redirects to login.
    """
    if request.user.is_authenticated:
        AuditService.log(
            action=AuditAction.LOGOUT,
            module='identity',
            entity_type='User',
            entity_id=str(request.user.pk),
            actor=request.user,
            request=request,
            reason='User logged out'
        )
        logout(request)

    # Completely purge session data and cookies
    request.session.flush()

    messages.info(request, 'You have been logged out successfully.')
    return redirect('login')
