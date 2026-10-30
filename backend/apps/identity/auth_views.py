"""
Web authentication views for School ERP.
Handles session-based login, logout, and password change screens.
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from apps.identity.models import User
from apps.school_config.models import SchoolProfile, AcademicYear
from apps.audit.models import AuditAction
from apps.audit.services import AuditService


def login_view(request):
    """
    Renders the web login page and processes session-based authentication.
    Supports login via username or email.
    """
    # If already logged in, redirect to home dashboard
    if request.user.is_authenticated:
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

        # Try authenticating directly by username
        user = authenticate(request, username=identifier, password=password)

        # If not authenticated and identifier looks like email, lookup username
        if user is None and '@' in identifier:
            try:
                user_obj = User.objects.get(email__iexact=identifier)
                user = authenticate(request, username=user_obj.username, password=password)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                user = None

        if user is None:
            # Check if candidate exists with valid password but is deactivated
            candidate = None
            try:
                if '@' in identifier:
                    candidate = User.objects.filter(email__iexact=identifier).first()
                else:
                    candidate = User.objects.filter(username=identifier).first()
            except Exception:
                pass

            if candidate and candidate.check_password(password) and not candidate.is_active:
                messages.error(request, 'Your account has been deactivated. Please contact school administration.')
                return render(request, 'auth/login.html', {
                    'school_profile': school_profile,
                    'current_year': current_year,
                    'username': identifier,
                    'next': next_url,
                })

            messages.error(request, 'Invalid credentials. Please verify your username and password.')
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

    messages.info(request, 'You have been logged out successfully.')
    return redirect('login')

