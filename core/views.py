from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from progress.models import TopicProgress
from quizzes.models import QuizAttempt
from progress.services import (
    record_topic_view,
    get_topic_display_status,
    get_subject_progress_summary,
    get_overall_learner_progress,
)
from access.services import has_subject_access, has_topic_access
@login_required
def learner_dashboard(request):
    user = request.user
    if user.is_admin_user and request.GET.get('view') != 'learner':
        # Admin logged in, but can stay or switch views easily
        pass
        
    # Filter subjects for learner based on access; admin sees all
    active_subjects = Subject.objects.filter(is_active=True)
    if not user.is_admin_user:
        # Only include subjects the learner is allowed to access
        active_subjects = [s for s in active_subjects if has_subject_access(user, s)]
    subjects_data = []
    
    for subject in active_subjects:
        summary = get_subject_progress_summary(user, subject)
        subjects_data.append({
            'subject': subject,
            'progress_percent': summary['progress_percent'],
            'understanding_percent': summary['understanding_percent'],
            'completed_topics_count': summary['completed_topics_count'],
            'total_topics_count': summary['total_topics_count'],
        })

    overall_progress = get_overall_learner_progress(user)
    
    context = {
        'subjects_data': subjects_data,
        'overall_progress': overall_progress,
    }
    return render(request, 'learner/dashboard.html', context)

@login_required
def subject_detail(request, slug):
    subject = get_object_or_404(Subject, slug=slug, is_active=True)
    # Access control: deny if learner lacks access to the subject
    if not has_subject_access(request.user, subject):
        raise PermissionDenied
    all_active_topics = list(subject.topics.filter(is_active=True).prefetch_related('questions'))
    if not request.user.is_admin_user:
        active_topics = [t for t in all_active_topics if has_topic_access(request.user, t)]
    else:
        active_topics = all_active_topics
    
    user_progresses = {
        p.topic_id: p 
        for p in TopicProgress.objects.filter(user=request.user, topic__in=active_topics)
    }
    
    topics_data = []
    for topic in active_topics:
        prog = user_progresses.get(topic.id)
        display_status = get_topic_display_status(prog)
        topics_data.append({
            'topic': topic,
            'progress': prog,
            'display_status': display_status,
            'best_score': prog.best_score if prog and prog.attempts_count > 0 else None,
            'is_ready': topic.is_assessment_ready(),
        })
        
    summary = get_subject_progress_summary(request.user, subject)
    
    context = {
        'subject': subject,
        'topics_data': topics_data,
        'summary': summary,
    }
    return render(request, 'learner/subject.html', context)

@login_required
def topic_detail(request, slug, topic_id):
    # Retrieve the topic first
    topic = get_object_or_404(Topic, id=topic_id, subject__slug=slug, is_active=True)
    # Access control: deny if learner lacks access to the topic
    if not has_topic_access(request.user, topic):
        raise PermissionDenied
    # Record view and flip status to in_progress if not_started
    progress = record_topic_view(request.user, topic)
    display_status = get_topic_display_status(progress)

    videos = list(topic.videos.all())
    resources = list(topic.resources.all())
    # Prepare attempt data with review-aware display


    attempts_qs = QuizAttempt.objects.filter(user=request.user, topic=topic).order_by('-created_at')
    attempts_data = []
    for a in attempts_qs:
        # Does any response still need manual review?
        requires_review = a.responses.filter(is_correct__isnull=True).exists()
        # Number of graded responses (is_correct not null)
        graded_count = a.responses.filter(is_correct__isnull=False).count()
        # Show numeric score only if at least one response is graded
        display_score = a.score if graded_count > 0 else None
        attempts_data.append({
            'attempt': a,
            'requires_review': requires_review,
            'graded': graded_count > 0,
            'display_score': display_score,
        })
    
    # Determine next topic (simple progression)
    next_topic = Topic.objects.filter(
        subject=topic.subject,
        is_active=True,
        order__gt=topic.order
    ).first()
    
    site_config = SiteConfig.get_solo()
    
    context = {
        'subject': topic.subject,
        'topic': topic,
        'progress': progress,
        'display_status': display_status,
        'videos': videos,
        'resources': resources,
        'attempts_data': attempts_data,
        'next_topic': next_topic,
        'effective_passing_score': topic.effective_passing_score,
        'required_question_count': site_config.default_required_question_count,
        'question_count': topic.questions.count(),
        'is_assessment_ready': topic.is_assessment_ready(),
    }
    return render(request, 'learner/topic.html', context)

@staff_member_required
def admin_dashboard(request):
    total_learners = User.objects.filter(role=User.ROLE_LEARNER).count()
    total_subjects = Subject.objects.count()
    total_topics = Topic.objects.count()
    
    learners = User.objects.filter(role=User.ROLE_LEARNER)
    learners_progress_data = []
    
    total_prog_sum = 0
    for learner in learners:
        prog = get_overall_learner_progress(learner)
        total_prog_sum += prog
        learners_progress_data.append({
            'learner': learner,
            'overall_progress': prog,
        })
        
    avg_progress = int(round(total_prog_sum / total_learners)) if total_learners > 0 else 0
    
    context = {
        'total_learners': total_learners,
        'total_subjects': total_subjects,
        'total_topics': total_topics,
        'avg_progress': avg_progress,
        'learners_progress_data': learners_progress_data,
    }
    return render(request, 'admin_dashboard/dashboard.html', context)

@staff_member_required
def admin_learner_progress_detail(request, user_id):
    learner = get_object_or_404(User, id=user_id)
    active_subjects = Subject.objects.filter(is_active=True)
    
    subjects_breakdown = []
    for subject in active_subjects:
        topics = subject.topics.filter(is_active=True)
        user_progs = {p.topic_id: p for p in TopicProgress.objects.filter(user=learner, topic__in=topics)}
        
        topics_info = []
        for topic in topics:
            prog = user_progs.get(topic.id)
            disp = get_topic_display_status(prog)
            topics_info.append({
                'topic': topic,
                'progress': prog,
                'display_status': disp,
            })
            
        summary = get_subject_progress_summary(learner, subject)
        subjects_breakdown.append({
            'subject': subject,
            'summary': summary,
            'topics_info': topics_info,
        })
        
    overall_progress = get_overall_learner_progress(learner)
    
    context = {
        'learner': learner,
        'subjects_breakdown': subjects_breakdown,
        'overall_progress': overall_progress,
    }
    return render(request, 'admin_dashboard/learner_progress.html', context)
