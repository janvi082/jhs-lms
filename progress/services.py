from django.utils import timezone
from django.db.models import Avg, Q
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, QuizAttempt
from .models import TopicProgress

def record_topic_view(user, topic):
    """
    Get or create TopicProgress when a learner visits a topic page.
    Flips not_started -> in_progress and updates last_accessed timestamp.
    """
    if not user.is_authenticated or not user.is_learner:
        return None
        
    progress, created = TopicProgress.objects.get_or_create(
        user=user,
        topic=topic,
        defaults={'status': TopicProgress.STATUS_IN_PROGRESS, 'last_accessed': timezone.now()}
    )
    
    if not created:
        if progress.status == TopicProgress.STATUS_NOT_STARTED:
            progress.status = TopicProgress.STATUS_IN_PROGRESS
        progress.last_accessed = timezone.now()
        progress.save(update_fields=['status', 'last_accessed'])
        
    return progress

def get_topic_display_status(progress):
    """
    Computes the 4 learner-facing display statuses:
    1. Not Started (○)
    2. In Progress (◐)
    3. Needs Improvement (◐ / ⚠️) - attempted but not passed
    4. Completed (✓)
    """
    if progress is None or progress.status == TopicProgress.STATUS_NOT_STARTED:
        return {
            'code': 'not_started',
            'label': 'Not Started',
            'icon': '○',
            'badge_class': 'bg-secondary',
            'text_class': 'text-muted'
        }
    
    if progress.status == TopicProgress.STATUS_COMPLETED:
        return {
            'code': 'completed',
            'label': 'Completed',
            'icon': '✓',
            'badge_class': 'bg-success',
            'text_class': 'text-success'
        }
        
    if progress.status == TopicProgress.STATUS_IN_PROGRESS:
        if progress.attempts_count > 0:
            return {
                'code': 'needs_improvement',
                'label': 'Needs Improvement',
                'icon': '◐',
                'badge_class': 'bg-warning text-dark',
                'text_class': 'text-warning'
            }
        return {
            'code': 'in_progress',
            'label': 'In Progress',
            'icon': '◐',
            'badge_class': 'bg-info text-dark',
            'text_class': 'text-info'
        }
        
    return {
        'code': 'not_started',
        'label': 'Not Started',
        'icon': '○',
        'badge_class': 'bg-secondary',
        'text_class': 'text-muted'
    }

def get_subject_progress_summary(user, subject):
    """
    Calculates Progress %, Understanding %, and Assessments Passed for a subject.
    """
    active_topics = subject.topics.filter(is_active=True)
    total_active_topics = active_topics.count()
    
    if total_active_topics == 0:
        return {
            'progress_percent': 0,
            'understanding_percent': None,
            'completed_topics_count': 0,
            'total_topics_count': 0,
            'assessments_passed_display': "0 / 0",
        }

    topic_ids = active_topics.values_list('id', flat=True)
    user_progresses = TopicProgress.objects.filter(user=user, topic_id__in=topic_ids) if user.is_authenticated else []
    progress_map = {p.topic_id: p for p in user_progresses}
    
    completed_count = sum(1 for p in user_progresses if p.status == TopicProgress.STATUS_COMPLETED)
    progress_percent = int(round((completed_count / total_active_topics) * 100))
    
    attempted_scores = [p.best_score for p in user_progresses if p.attempts_count > 0]
    if attempted_scores:
        avg_score = sum(attempted_scores) / len(attempted_scores)
        understanding_percent = int(round(avg_score))
    else:
        understanding_percent = None

    return {
        'progress_percent': progress_percent,
        'understanding_percent': understanding_percent,
        'completed_topics_count': completed_count,
        'total_topics_count': total_active_topics,
        'assessments_passed_display': f"{completed_count} / {total_active_topics}",
    }

def get_overall_learner_progress(user):
    """
    Calculates overall average progress % across all active subjects.
    """
    active_subjects = Subject.objects.filter(is_active=True)
    if not active_subjects.exists():
        return 0
        
    subject_progresses = [
        get_subject_progress_summary(user, sub)['progress_percent']
        for sub in active_subjects
    ]
    
    return int(round(sum(subject_progresses) / len(subject_progresses)))

def submit_quiz_attempt(user, topic, submitted_answers):
    """
    Grading engine and attempt logging.
    submitted_answers: dict mapping question_id (int) -> selected_choice_id (int)
    """
    questions = list(topic.questions.prefetch_related('choices').all())
    total_questions = len(questions)
    
    if total_questions == 0:
        raise ValueError("Cannot take quiz on a topic with zero questions.")
        
    correct_count = 0
    for q in questions:
        selected_choice_id = submitted_answers.get(q.id) or submitted_answers.get(str(q.id))
        if selected_choice_id:
            try:
                selected_choice_id = int(selected_choice_id)
                correct_choice = next((c for c in q.choices.all() if c.is_correct), None)
                if correct_choice and correct_choice.id == selected_choice_id:
                    correct_count += 1
            except (ValueError, TypeError):
                pass

    score = int(round((correct_count / total_questions) * 100))
    passing_score_used = topic.effective_passing_score
    passed = score >= passing_score_used
    
    previous_attempts_count = QuizAttempt.objects.filter(user=user, topic=topic).count()
    attempt_number = previous_attempts_count + 1
    
    attempt = QuizAttempt.objects.create(
        user=user,
        topic=topic,
        attempt_number=attempt_number,
        correct_count=correct_count,
        total_questions=total_questions,
        score=score,
        passed=passed,
        passing_score_used=passing_score_used
    )
    
    tp, _ = TopicProgress.objects.get_or_create(user=user, topic=topic)
    tp.attempts_count += 1
    tp.latest_score = score
    tp.best_score = max(tp.best_score, score)
    
    if passed:
        tp.status = TopicProgress.STATUS_COMPLETED
        if tp.completed_at is None:
            tp.completed_at = timezone.now()
    else:
        if tp.status == TopicProgress.STATUS_NOT_STARTED:
            tp.status = TopicProgress.STATUS_IN_PROGRESS
            
    tp.save()
    return attempt, tp
