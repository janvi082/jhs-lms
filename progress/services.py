from django.utils import timezone
from django.db.models import Avg, Q
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, QuizAttempt
from .models import TopicProgress

def get_effective_passing_score(topic):
    return topic.effective_passing_score

def _get_effective_progression_sequence(user, subject):
    from access.services import has_topic_access
    active_topics = list(Topic.objects.filter(subject=subject, is_active=True).order_by('order', 'id'))
    return [t for t in active_topics if has_topic_access(user, t)]

def get_next_topic(user, topic):
    effective_topics = _get_effective_progression_sequence(user, topic.subject)
    for i, t in enumerate(effective_topics):
        if t.id == topic.id:
            if i + 1 < len(effective_topics):
                return effective_topics[i + 1]
            return None
    return None

def is_topic_unlocked(user, topic):
    from access.services import has_topic_access
    if not has_topic_access(user, topic):
        return False

    if getattr(user, 'is_admin_user', False):
        return True

    if TopicProgress.objects.filter(
        user=user, 
        topic=topic, 
        status__in=[TopicProgress.STATUS_COMPLETED, TopicProgress.STATUS_IN_PROGRESS]
    ).exists():
        return True

    effective_topics = _get_effective_progression_sequence(user, topic.subject)
    
    previous_topic = None
    for i, t in enumerate(effective_topics):
        if t.id == topic.id:
            if i > 0:
                previous_topic = effective_topics[i - 1]
            break

    if not previous_topic:
        return True

    return TopicProgress.objects.filter(
        user=user,
        topic=previous_topic,
        status=TopicProgress.STATUS_COMPLETED
    ).exists()

def complete_non_assessed_topic(user, topic):
    from access.services import has_subject_access, has_topic_access
    if not topic.assessment_required:
        if has_subject_access(user, topic.subject) and has_topic_access(user, topic):
            if is_topic_unlocked(user, topic):
                progress, _ = TopicProgress.objects.get_or_create(user=user, topic=topic)
                if progress.status != TopicProgress.STATUS_COMPLETED:
                    progress.status = TopicProgress.STATUS_COMPLETED
                    if progress.completed_at is None:
                        progress.completed_at = timezone.now()
                    progress.save(update_fields=['status', 'completed_at'])

def record_topic_view(user, topic):
    """
    Get or create TopicProgress when a learner visits a topic page.
    Flips not_started -> in_progress and updates last_accessed timestamp.
    """
    if not user.is_authenticated or not user.is_learner:
        return None
        
    if not is_topic_unlocked(user, topic):
        return TopicProgress.objects.filter(user=user, topic=topic).first()

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
        
    complete_non_assessed_topic(user, topic)
    progress.refresh_from_db()

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
    all_active_topics = subject.topics.filter(is_active=True)
    if user.is_authenticated and not user.is_admin_user:
        from access.services import has_topic_access
        active_topics = [t for t in all_active_topics if has_topic_access(user, t)]
    else:
        active_topics = list(all_active_topics)

    total_active_topics = len(active_topics)
    
    if total_active_topics == 0:
        return {
            'progress_percent': 0,
            'understanding_percent': None,
            'completed_topics_count': 0,
            'total_topics_count': 0,
            'assessments_passed_display': "0 / 0",
        }

    topic_ids = [t.id for t in active_topics]
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
    Grading engine and attempt logging supporting 5 question types:
    Single Choice, Multiple Choice, True/False, Short Answer, Paragraph.
    Permanently creates QuizResponse records for every question.
    """
    from quizzes.models import QuizResponse

    questions = list(topic.questions.prefetch_related('choices').all())
    total_questions = len(questions)
    
    if total_questions == 0:
        raise ValueError("Cannot take quiz on a topic with zero questions.")
        
    def get_post_val(key):
        if hasattr(submitted_answers, 'getlist'):
            vals = submitted_answers.getlist(key) or submitted_answers.getlist(str(key))
            if vals:
                return vals
        v = submitted_answers.get(key)
        if v is None:
            v = submitted_answers.get(str(key))
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return v
        return [v]

    correct_count = 0
    gradable_count = 0
    response_objects = []

    for q in questions:
        raw_vals = get_post_val(q.id)
        is_correct = None
        text_resp = ''
        selected_choice_ids = []

        if q.question_type in (Question.TYPE_SINGLE_CHOICE, Question.TYPE_TRUE_FALSE):
            # Only process if an answer was provided.
            if raw_vals and raw_vals[0]:
                # Count as gradable
                gradable_count += 1
                try:
                    selected_choice_id = int(raw_vals[0])
                    selected_choice_ids = [selected_choice_id]
                    correct_choice = next((c for c in q.choices.all() if c.is_correct), None)
                    if correct_choice and correct_choice.id == selected_choice_id:
                        is_correct = True
                        correct_count += 1
                    else:
                        is_correct = False
                except (ValueError, TypeError):
                    is_correct = False
            else:
                # No answer supplied – treat as not responded
                is_correct = None

        elif q.question_type == Question.TYPE_MULTIPLE_CHOICE:
            # Only process if answers were provided.
            if raw_vals:
                # Count as gradable
                gradable_count += 1
                try:
                    selected_choice_ids = [int(v) for v in raw_vals if v]
                    selected_set = set(selected_choice_ids)
                    correct_set = {c.id for c in q.choices.all() if c.is_correct}
                    if selected_set and selected_set == correct_set:
                        is_correct = True
                        correct_count += 1
                    else:
                        is_correct = False
                except (ValueError, TypeError):
                    is_correct = False
            else:
                # No answer supplied – treat as not responded
                is_correct = None

        elif q.question_type == Question.TYPE_SHORT_ANSWER:
            gradable_count += 1
            text_resp = raw_vals[0] if raw_vals else ''
            trimmed_text = text_resp.strip()
            accepted = q.get_accepted_answers_list()
            if trimmed_text and any(trimmed_text.lower() == acc.strip().lower() for acc in accepted):
                is_correct = True
                correct_count += 1
            else:
                is_correct = False

        elif q.question_type == Question.TYPE_PARAGRAPH:
            text_resp = raw_vals[0] if raw_vals else ''
            is_correct = None

        # Append response only if it should be recorded:
        # - For paragraph questions, always record (is_correct is None).
        # - For other question types, record only if is_correct is not None (i.e., an answer was provided).
        if q.question_type == Question.TYPE_PARAGRAPH:
            response_objects.append((q, is_correct, text_resp, selected_choice_ids))
        elif is_correct is not None:
            response_objects.append((q, is_correct, text_resp, selected_choice_ids))

    if gradable_count > 0:
        score = int(round((correct_count / gradable_count) * 100))
    else:
        # No automatically gradable questions; set score to 0 to indicate N/A
        score = 0

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

    for q, is_corr, txt, choice_ids in response_objects:
        resp = QuizResponse.objects.create(
            attempt=attempt,
            question=q,
            is_correct=is_corr,
            text_response=txt
        )
        if choice_ids:
            valid_choices = [c for c in q.choices.all() if c.id in choice_ids]
            resp.selected_choices.set(valid_choices)
    
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

def recalculate_attempt_score(attempt):
    """Recalculate correct/wrong counts, score, and pass/fail for an existing attempt.
    Does NOT modify total_questions (original number of questions)."""
    correct = attempt.responses.filter(is_correct=True).count()
    wrong = attempt.responses.filter(is_correct=False).count()
    gradable = correct + wrong
    attempt.correct_count = correct
    # total_questions remains unchanged
    if gradable > 0:
        attempt.score = int(round((correct / gradable) * 100))
    else:
        # No gradable questions – keep numeric 0; UI will show N/A
        attempt.score = 0
    # Pass only if there is a numeric score and meets threshold
    attempt.passed = (attempt.score >= attempt.passing_score_used) if gradable > 0 else False
    attempt.save()

    # Synchronize TopicProgress.best_score based on all graded attempts for this learner and topic
    from django.db.models import Max
    from quizzes.models import QuizAttempt
    best = QuizAttempt.objects.filter(
        user=attempt.user,
        topic=attempt.topic,
        responses__is_correct__isnull=False
    ).distinct().aggregate(Max('score'))['score__max']
    best_score = best if best is not None else 0
    from .models import TopicProgress
    tp, _ = TopicProgress.objects.get_or_create(user=attempt.user, topic=attempt.topic)
    tp.best_score = best_score
    tp.latest_score = attempt.score

    if tp.best_score >= attempt.topic.effective_passing_score:
        if not attempt.responses.filter(is_correct__isnull=True).exists():
            if tp.status != TopicProgress.STATUS_COMPLETED:
                tp.status = TopicProgress.STATUS_COMPLETED
                if tp.completed_at is None:
                    tp.completed_at = timezone.now()

    tp.save()


