from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.contrib import messages
from .admin_site import super_admin_site

from core.views import (
    learner_dashboard,
    subject_detail,
    topic_detail,
)
from quizzes.views import quiz_modal, quiz_submit, quiz_result

# Deprecated custom admin wrapper removed; using SuperUserAdminSite directly


urlpatterns = [
    # Gated superuser developer access for Django Admin
    path('admin/', super_admin_site.urls),
    
    # Custom Non-Technical Admin Portal
    path('portal/', include('portal.urls')),
    
    # Authentication
    path('', include('accounts.urls')),
    
    # Learner Facing Views
    path('', learner_dashboard, name='learner_dashboard'),
    path('subject/<slug:slug>/', subject_detail, name='subject_detail'),
    path('subject/<slug:slug>/topic/<int:topic_id>/', topic_detail, name='topic_detail'),
    path('subject/<slug:slug>/topic/<int:topic_id>/quiz/', quiz_modal, name='quiz_modal'),
    path('subject/<slug:slug>/topic/<int:topic_id>/quiz/result/<int:attempt_id>/', quiz_result, name='quiz_result'),
    path('quiz/<int:topic_id>/submit/', quiz_submit, name='quiz_submit'),
]
