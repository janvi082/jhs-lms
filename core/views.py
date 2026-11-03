from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from accounts.models import User
from content.models import Subject, Topic, SiteConfig, Resource, ResourceAcknowledgement
from progress.models import TopicProgress
from quizzes.models import QuizAttempt
from progress.services import (
    record_topic_view,
    get_topic_display_status,
    get_subject_progress_summary,
    get_overall_learner_progress,
    is_topic_unlocked,
    get_topic_access_state,
    TopicAccessState,
    get_next_topic,
    reconcile_topic_progress,
    get_sequential_lock_reason,
)
from access.services import has_subject_access, has_topic_access, has_video_access, has_resource_access
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction, IntegrityError
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
    
    from progress.services import annotate_progress_with_review_status
    
    base_qs = TopicProgress.objects.filter(user=request.user, topic__in=active_topics)
    annotated_qs = annotate_progress_with_review_status(base_qs)
    
    user_progresses = {
        p.topic_id: p 
        for p in annotated_qs
    }
    
    topics_data = []
    for topic in active_topics:
        prog = user_progresses.get(topic.id)
        display_status = get_topic_display_status(prog)
        state = get_topic_access_state(request.user, topic)
        topics_data.append({
            'topic': topic,
            'progress': prog,
            'display_status': display_status,
            'best_score': prog.best_score if prog and prog.attempts_count > 0 and getattr(prog, 'has_finalized_score', True) else None,
            'is_ready': topic.is_assessment_ready(),
            'access_state': state,
            'lock_reason': get_sequential_lock_reason(request.user, topic) if state == TopicAccessState.LOCKED_SEQUENTIAL else None,
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
    # Access state handling
    access_state = get_topic_access_state(request.user, topic)
    if access_state in (TopicAccessState.DENIED, TopicAccessState.UPCOMING):
        raise PermissionDenied

    if access_state == TopicAccessState.LOCKED_SEQUENTIAL:
        progress = TopicProgress.objects.filter(user=request.user, topic=topic).first()
        if request.user.is_admin_user:
            videos = list(topic.videos.all())
            resources = list(topic.resources.all())
        else:
            videos = [v for v in topic.videos.all() if has_video_access(request.user, v)]
            resources = [r for r in topic.resources.all() if has_resource_access(request.user, r)]
        for v in videos:
            v.url = ""
        for r in resources:
            r.url = ""
    else:
        # Record view and flip status to in_progress if not_started
        progress = record_topic_view(request.user, topic)
        if request.user.is_admin_user:
            videos = list(topic.videos.all())
            resources = list(topic.resources.all())
        else:
            videos = [v for v in topic.videos.all() if has_video_access(request.user, v)]
            resources = [r for r in topic.resources.all() if has_resource_access(request.user, r)]

    display_status = get_topic_display_status(progress)
    # Prepare attempt data with review-aware display

    attempts_qs = QuizAttempt.objects.filter(user=request.user, topic=topic).order_by('-created_at')
    attempts_data = []
    for a in attempts_qs:
        # Does any response still need manual review?
        requires_review = a.responses.filter(is_correct__isnull=True).exists()
        # Number of graded responses (is_correct not null)
        graded_count = a.responses.filter(is_correct__isnull=False).count()
        # Is it a legacy attempt without individual response records?
        is_legacy = not a.responses.exists()
        
        # Determine if we should show the score
        is_graded = (graded_count > 0) or is_legacy
        
        display_score = a.score if (is_graded and a.score is not None) else None
        
        attempts_data.append({
            'attempt': a,
            'requires_review': requires_review,
            'graded': is_graded and display_score is not None,
            'display_score': display_score,
        })
    
    # Determine next topic (simple progression)
    next_topic = get_next_topic(request.user, topic)
    
    # Build acknowledgement map for current user
    acks = {
        ack.resource_id: ack
        for ack in ResourceAcknowledgement.objects.filter(
            user=request.user,
            resource__in=resources
        )
    }
    for r in resources:
        ack = acks.get(r.id)
        if not ack:
            r.ack_status = 'pending'
            r.ack_obj = None
        elif ack.acknowledged_version == r.version:
            r.ack_status = 'acknowledged'
            r.ack_obj = ack
        else:
            r.ack_status = 'updated'
            r.ack_obj = ack

    site_config = SiteConfig.get_solo()
    
    context = {
        'subject': topic.subject,
        'topic': topic,
        'access_state': access_state,
        'lock_reason': get_sequential_lock_reason(request.user, topic) if access_state == TopicAccessState.LOCKED_SEQUENTIAL else None,
        'next_topic_lock_reason': get_sequential_lock_reason(request.user, next_topic) if (next_topic and get_topic_access_state(request.user, next_topic) == TopicAccessState.LOCKED_SEQUENTIAL) else None,
        'progress': progress,
        'display_status': display_status,
        'videos': videos,
        'resources': resources,
        'attempts_data': attempts_data,
        'next_topic': next_topic,
        'next_topic_unlocked': (
            get_topic_access_state(request.user, next_topic)
            not in (TopicAccessState.DENIED, TopicAccessState.UPCOMING, TopicAccessState.LOCKED_SEQUENTIAL)
        ) if next_topic else False,
        'effective_passing_score': topic.effective_passing_score,
        'required_question_count': site_config.default_required_question_count,
        'question_count': topic.questions.filter(is_active=True).count(),
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


def _is_expected_acknowledgement_unique_violation(exc):
    """
    Reliably determine if an IntegrityError was caused by the expected unique
    constraint on (user, resource) for ResourceAcknowledgement across supported backends.
    """
    cause = getattr(exc, '__cause__', None)
    # 1. PostgreSQL (via psycopg2 / Neon) exposes exact constraint name on diag
    if cause is not None:
        diag = getattr(cause, 'diag', None)
        if diag is not None:
            constraint_name = getattr(diag, 'constraint_name', None)
            if constraint_name == 'unique_user_resource_acknowledgement':
                return True

    # 2. SQLite / fallback message inspection: explicitly verify table and unique columns/constraint
    err_msg = str(exc).lower()
    if 'unique' in err_msg and ('unique_user_resource_acknowledgement' in err_msg or ('content_resourceacknowledgement' in err_msg and 'user_id' in err_msg and 'resource_id' in err_msg)):
        return True

    return False


@login_required
@require_POST
def acknowledge_material(request, resource_id):
    resource = get_object_or_404(Resource, id=resource_id, topic__is_active=True)
    topic = resource.topic
    user = request.user

    # Permission check: must have access to topic and resource
    if not has_topic_access(user, topic) or not has_resource_access(user, resource):
        raise PermissionDenied("You do not have permission to access this learning material.")

    # Access state check: sequential lock or denied must block acknowledgement
    access_state = get_topic_access_state(user, topic)
    if access_state in (TopicAccessState.DENIED, TopicAccessState.UPCOMING, TopicAccessState.LOCKED_SEQUENTIAL):
        raise PermissionDenied("This topic is currently locked or inaccessible.")

    # Concurrency-safe write handling first-time creation race and updates
    now = timezone.now()
    try:
        with transaction.atomic():
            ack = ResourceAcknowledgement.objects.select_for_update().filter(
                user=user,
                resource=resource
            ).first()

            if ack is None:
                ResourceAcknowledgement.objects.create(
                    user=user,
                    resource=resource,
                    acknowledged_version=resource.version,
                    acknowledged_at=now
                )
                messages.success(request, f"Review confirmed for '{resource.title}'.")
            elif ack.acknowledged_version != resource.version:
                ack.acknowledged_version = resource.version
                ack.acknowledged_at = now
                ack.save(update_fields=['acknowledged_version', 'acknowledged_at'])
                messages.success(request, f"Updated review confirmed for '{resource.title}'.")
            else:
                # Already acknowledged at current version: idempotent no-op (do not rewrite timestamp)
                messages.info(request, f"Review for '{resource.title}' is already up to date.")
    except IntegrityError as exc:
        # Recover only if the error is genuinely the expected unique constraint on (user, resource)
        if not _is_expected_acknowledgement_unique_violation(exc):
            raise

        # Re-read outside the failed atomic block within a clean atomic transaction
        with transaction.atomic():
            ack = ResourceAcknowledgement.objects.select_for_update().filter(
                user=user,
                resource=resource
            ).first()
            if ack is None:
                # No record exists despite constraint match; re-raise safely
                raise
            if ack.acknowledged_version != resource.version:
                ack.acknowledged_version = resource.version
                ack.acknowledged_at = now
                ack.save(update_fields=['acknowledged_version', 'acknowledged_at'])
                messages.success(request, f"Updated review confirmed for '{resource.title}'.")
            else:
                messages.info(request, f"Review for '{resource.title}' is already up to date.")

    # Reconcile topic progress to evaluate completion under configurable rules
    reconcile_topic_progress(user, topic)

    target_url = reverse('topic_detail', args=[topic.subject.slug, topic.id])
    return redirect(f"{target_url}#material-{resource.id}")
