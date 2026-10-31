from datetime import timedelta
from django.utils import timezone
from django.db import transaction
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

class TopicAccessState:
    DENIED = 'DENIED'
    UPCOMING = 'UPCOMING'
    LOCKED_SEQUENTIAL = 'LOCKED_SEQUENTIAL'
    AVAILABLE = 'AVAILABLE'
    COMPLETED = 'COMPLETED'

def _is_sequentially_unlocked(user, topic):
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

def get_topic_access_state(user, topic):
    from access.services import has_topic_access
    if not has_topic_access(user, topic):
        return TopicAccessState.DENIED

    if getattr(user, 'is_admin_user', False):
        return TopicAccessState.AVAILABLE

    progress = TopicProgress.objects.filter(user=user, topic=topic).first()
    if progress:
        if progress.status == TopicProgress.STATUS_COMPLETED:
            return TopicAccessState.COMPLETED
        if progress.status == TopicProgress.STATUS_IN_PROGRESS:
            return TopicAccessState.AVAILABLE

    if topic.learning_phase_id:
        date_of_joining = getattr(user, 'date_of_joining', None)
        if date_of_joining is None:
            return TopicAccessState.DENIED

        learner_day = (timezone.localdate() - date_of_joining).days + 1
        if learner_day < topic.learning_phase.start_day:
            return TopicAccessState.UPCOMING

    if _is_sequentially_unlocked(user, topic):
        return TopicAccessState.AVAILABLE

    return TopicAccessState.LOCKED_SEQUENTIAL

def is_topic_unlocked(user, topic):
    state = get_topic_access_state(user, topic)
    return state in (TopicAccessState.AVAILABLE, TopicAccessState.COMPLETED)

def _mark_topic_completed_locked(progress, topic, user, completion_time=None):
    """
    Internal helper to mark a locked TopicProgress instance as COMPLETED.
    Must be called within an active transaction holding select_for_update on progress.
    """
    if topic.assessment_required and topic.active_question_count > 0:
        return progress

    if completion_time is None:
        completion_time = timezone.now()

    is_first_completion = (progress.status != TopicProgress.STATUS_COMPLETED)

    if is_first_completion:
        doj = getattr(user, 'date_of_joining', None)
        if doj is None:
            if progress.status == TopicProgress.STATUS_NOT_STARTED:
                progress.status = TopicProgress.STATUS_IN_PROGRESS
                progress.save(update_fields=['status'])
            return progress

        progress.status = TopicProgress.STATUS_COMPLETED
        # Option A: genuine first completion sets legitimate completion timestamp
        progress.completed_at = completion_time
        # Option A: capture nominal deadline, replacing any stale deadline
        end_day = (
            progress.expected_end_day_snapshot
            if (progress.expected_end_day_snapshot is not None and progress.expected_end_day_snapshot >= 1)
            else (topic.learning_phase.end_day if (topic.learning_phase_id and topic.learning_phase) else None)
        )
        progress.frozen_deadline = calculate_nominal_deadline(doj, end_day)
        update_fields = ['status', 'completed_at', 'frozen_deadline']
        if progress.frozen_deadline is not None:
            progress.timing_provenance = TopicProgress.PROVENANCE_SYSTEM_CAPTURED
            update_fields.append('timing_provenance')
        if (progress.expected_end_day_snapshot is None or progress.expected_end_day_snapshot < 1) and end_day is not None and end_day >= 1:
            progress.expected_end_day_snapshot = end_day
            update_fields.append('expected_end_day_snapshot')
        progress.save(update_fields=update_fields)

    return progress

def complete_non_assessed_topic(user, topic):
    from access.services import has_subject_access, has_topic_access
    if not topic.assessment_required or topic.active_question_count == 0:
        if has_subject_access(user, topic.subject) and has_topic_access(user, topic):
            if is_topic_unlocked(user, topic):
                with transaction.atomic():
                    expected_end_day = topic.learning_phase.end_day if topic.learning_phase_id else None
                    progress, _ = TopicProgress.objects.get_or_create(
                        user=user,
                        topic=topic,
                        defaults={
                            'expected_end_day_snapshot': expected_end_day
                        }
                    )
                    progress = TopicProgress.objects.select_for_update().get(id=progress.id)
                    _mark_topic_completed_locked(progress, topic, user)
                    return progress
    return None

def record_topic_view(user, topic):
    """
    Get or create TopicProgress when a learner visits a topic page.
    Flips not_started -> in_progress and updates last_accessed timestamp.
    """
    if not user.is_authenticated or not user.is_learner:
        return None

    if not is_topic_unlocked(user, topic):
        return TopicProgress.objects.filter(user=user, topic=topic).first()

    with transaction.atomic():
        expected_end_day = topic.learning_phase.end_day if topic.learning_phase_id else None
        progress, created = TopicProgress.objects.get_or_create(
            user=user,
            topic=topic,
            defaults={
                'status': TopicProgress.STATUS_IN_PROGRESS,
                'last_accessed': timezone.now(),
                'expected_end_day_snapshot': expected_end_day
            }
        )
        progress = TopicProgress.objects.select_for_update().get(id=progress.id)

        update_fields = ['last_accessed']
        progress.last_accessed = timezone.now()
        if progress.status == TopicProgress.STATUS_NOT_STARTED:
            progress.status = TopicProgress.STATUS_IN_PROGRESS
            update_fields.append('status')
        progress.save(update_fields=update_fields)

        from access.services import has_subject_access, has_topic_access
        if not topic.assessment_required or topic.active_question_count == 0:
            if has_subject_access(user, topic.subject) and has_topic_access(user, topic):
                _mark_topic_completed_locked(progress, topic, user, completion_time=progress.last_accessed)

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

    if getattr(progress, 'has_pending_review', False):
        return {
            'code': 'pending_review',
            'label': 'Pending Review',
            'icon': '⏳',
            'badge_class': 'bg-info text-dark',
            'text_class': 'text-info'
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

def annotate_progress_with_review_status(queryset):
    """
    Annotates a TopicProgress queryset with:
    - has_pending_review: True if any attempt has responses with is_correct=None
    - has_finalized_score: True if there is at least one attempt with no pending responses
    """
    from quizzes.models import QuizAttempt, QuizResponse
    from django.db.models import Exists, OuterRef

    pending_responses = QuizResponse.objects.filter(
        attempt_id=OuterRef('pk'),
        is_correct__isnull=True
    )

    finalized_attempts = QuizAttempt.objects.filter(
        user=OuterRef('user'),
        topic_id=OuterRef('topic_id')
    ).annotate(
        has_pending=Exists(pending_responses)
    ).filter(has_pending=False)

    pending_attempts = QuizAttempt.objects.filter(
        user=OuterRef('user'),
        topic_id=OuterRef('topic_id')
    ).annotate(
        has_pending=Exists(pending_responses)
    ).filter(has_pending=True)

    return queryset.annotate(
        has_finalized_score=Exists(finalized_attempts),
        has_pending_review=Exists(pending_attempts)
    )

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

    if user.is_authenticated:
        base_qs = TopicProgress.objects.filter(user=user, topic_id__in=topic_ids)
        user_progresses = list(annotate_progress_with_review_status(base_qs))
    else:
        user_progresses = []

    progress_map = {p.topic_id: p for p in user_progresses}

    completed_count = sum(1 for p in user_progresses if p.status == TopicProgress.STATUS_COMPLETED)
    progress_percent = int(round((completed_count / total_active_topics) * 100))

    attempted_scores = [p.best_score for p in user_progresses if p.attempts_count > 0 and getattr(p, 'has_finalized_score', True)]
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

def calculate_reconciled_fields(
    topic_assessment_required,
    attempts_data,
    current_status,
    current_completed_at,
    current_frozen_deadline=None,
    date_of_joining=None,
    phase_end_day=None,
    expected_end_day_snapshot=None,
    current_timing_provenance=None
):
    from progress.models import TopicProgress

    attempts_count = len(attempts_data)
    fully_graded = [a for a in attempts_data if a['is_fully_graded']]

    if fully_graded:
        best_score = max(a['score'] for a in fully_graded)
        latest_score = fully_graded[-1]['score']
    else:
        best_score = 0
        latest_score = 0

    status = current_status
    completed_at = current_completed_at
    frozen_deadline = current_frozen_deadline
    snapshot = expected_end_day_snapshot
    timing_provenance = current_timing_provenance

    if topic_assessment_required:
        has_passed = any(a['passed'] for a in fully_graded)
        if has_passed:
            is_first_completion = (current_status != TopicProgress.STATUS_COMPLETED)
            earliest_passed = next(a for a in fully_graded if a['passed'])

            if is_first_completion:
                if date_of_joining is None:
                    # Missing DOJ: do not finalize topic completion.
                    # Retain IN_PROGRESS, do not set completed_at or frozen_deadline.
                    status = TopicProgress.STATUS_IN_PROGRESS
                    completed_at = None
                    frozen_deadline = None
                    timing_provenance = None
                else:
                    status = TopicProgress.STATUS_COMPLETED
                    completed_at = earliest_passed['created_at']
                    # Determine authoritative end day: prioritize snapshot if valid (>= 1)
                    effective_end_day = (
                        expected_end_day_snapshot
                        if (expected_end_day_snapshot is not None and expected_end_day_snapshot >= 1)
                        else phase_end_day
                    )
                    frozen_deadline = calculate_nominal_deadline(date_of_joining, effective_end_day)
                    if frozen_deadline is not None:
                        timing_provenance = TopicProgress.PROVENANCE_SYSTEM_CAPTURED
                    else:
                        timing_provenance = None
                    if (snapshot is None or snapshot < 1) and phase_end_day is not None and phase_end_day >= 1:
                        snapshot = phase_end_day
            else:
                # Legacy repair & Immutability:
                status = TopicProgress.STATUS_COMPLETED
                if completed_at is None:
                    completed_at = earliest_passed['created_at']
                # frozen_deadline and timing_provenance are preserved (never backfilled, never overwritten)
                timing_provenance = current_timing_provenance

        elif current_status == TopicProgress.STATUS_COMPLETED:
            # Option 2B (Status-Trust): Routine reconciliation trusts an established completion
            status = TopicProgress.STATUS_COMPLETED
            # completed_at, frozen_deadline, and timing_provenance remain unchanged
            timing_provenance = current_timing_provenance

        elif attempts_count > 0:
            status = TopicProgress.STATUS_IN_PROGRESS
            completed_at = None
            frozen_deadline = None
            timing_provenance = None
        else:
            status = TopicProgress.STATUS_NOT_STARTED
            completed_at = None
            frozen_deadline = None
            timing_provenance = None

    return {
        'attempts_count': attempts_count,
        'best_score': best_score,
        'latest_score': latest_score,
        'status': status,
        'completed_at': completed_at,
        'frozen_deadline': frozen_deadline,
        'expected_end_day_snapshot': snapshot,
        'timing_provenance': timing_provenance,
    }

def reconcile_topic_progress(user, topic):
    from quizzes.models import QuizAttempt
    from django.db.models import Count, Q

    with transaction.atomic():
        expected_end_day = topic.learning_phase.end_day if topic.learning_phase_id else None
        tp, _ = TopicProgress.objects.get_or_create(
            user=user,
            topic=topic,
            defaults={
                'expected_end_day_snapshot': expected_end_day
            }
        )
        tp = TopicProgress.objects.select_for_update().get(id=tp.id)

        attempts = QuizAttempt.objects.filter(user=user, topic=topic).annotate(
            ungraded_count=Count('responses', filter=Q(responses__is_correct__isnull=True))
        ).order_by('created_at', 'id')

        attempts_data = [
            {
                'score': a.score,
                'passed': a.passed,
                'created_at': a.created_at,
                'is_fully_graded': a.ungraded_count == 0
            }
            for a in attempts
        ]

        phase_end_day = topic.learning_phase.end_day if (topic.learning_phase_id and topic.learning_phase) else None
        doj = getattr(user, 'date_of_joining', None)

        result = calculate_reconciled_fields(
            topic_assessment_required=topic.assessment_required and topic.questions.filter(is_active=True).exists(),
            attempts_data=attempts_data,
            current_status=tp.status,
            current_completed_at=tp.completed_at,
            current_frozen_deadline=tp.frozen_deadline,
            date_of_joining=doj,
            phase_end_day=phase_end_day,
            expected_end_day_snapshot=tp.expected_end_day_snapshot,
            current_timing_provenance=tp.timing_provenance,
        )

        tp.attempts_count = result['attempts_count']
        tp.best_score = result['best_score']
        tp.latest_score = result['latest_score']
        tp.status = result['status']
        tp.completed_at = result['completed_at']
        tp.frozen_deadline = result['frozen_deadline']
        tp.timing_provenance = result['timing_provenance']

        update_fields = [
            'attempts_count',
            'best_score',
            'latest_score',
            'status',
            'completed_at',
            'frozen_deadline',
            'timing_provenance',
        ]
        if 'expected_end_day_snapshot' in result and result['expected_end_day_snapshot'] != tp.expected_end_day_snapshot:
            tp.expected_end_day_snapshot = result['expected_end_day_snapshot']
            update_fields.append('expected_end_day_snapshot')

        tp.save(update_fields=update_fields)
        return tp

def submit_quiz_attempt(user, topic, submitted_answers, session_question_ids=None):
    """
    Grading engine and attempt logging supporting 5 question types:
    Single Choice, Multiple Choice, True/False, Short Answer, Paragraph.
    Permanently creates QuizResponse records for every question.
    """
    from quizzes.models import QuizResponse

    if session_question_ids is not None:
        qs = topic.questions.filter(id__in=session_question_ids).prefetch_related('choices')
        q_map = {q.id: q for q in qs}
        questions = [q_map[qid] for qid in session_question_ids if qid in q_map]
    else:
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

    from django.core.exceptions import ValidationError

    # VALIDATION PASS
    for q in questions:
        raw_vals = get_post_val(q.id)
        if q.required:
            is_answered = False
            if q.question_type in (Question.TYPE_SINGLE_CHOICE, Question.TYPE_MULTIPLE_CHOICE, Question.TYPE_TRUE_FALSE):
                is_answered = bool(raw_vals and any(str(v).strip() for v in raw_vals))
            else:
                is_answered = bool(raw_vals and raw_vals[0].strip())

            if not is_answered:
                raise ValidationError(f"Question '{q.text}' is required.")

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
            text_resp = raw_vals[0] if raw_vals else ''
            trimmed_text = text_resp.strip()
            if not trimmed_text and not q.required:
                is_correct = None
            else:
                gradable_count += 1
                accepted = q.get_accepted_answers_list()
                if trimmed_text and any(trimmed_text.lower() == acc.strip().lower() for acc in accepted):
                    is_correct = True
                    correct_count += 1
                else:
                    is_correct = False

        elif q.question_type == Question.TYPE_PARAGRAPH:
            text_resp = raw_vals[0] if raw_vals else ''
            trimmed_text = text_resp.strip()
            if not trimmed_text and not q.required:
                pass # remains is_correct = None, but we will skip appending it
            else:
                is_correct = None

        # Append response only if it should be recorded:
        if q.question_type == Question.TYPE_PARAGRAPH:
            if text_resp.strip() or q.required:
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

    with transaction.atomic():
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

        tp = reconcile_topic_progress(user, topic)
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
    # Pass only if there is a numeric score and meets the threshold saved at attempt time
    attempt.passed = (attempt.score >= attempt.passing_score_used) if gradable > 0 else False
    attempt.save()

    tp = reconcile_topic_progress(attempt.user, attempt.topic)


# ==============================================================================
# LEARNING PHASE PERFORMANCE MONITORING HELPERS (STAGE 1 - UNIFIED ENGINE)
# ==============================================================================

class PhasePerformanceStatus:
    """Canonical performance status keys for Learning Phase monitoring."""
    EMPTY = 'empty'
    UPCOMING = 'upcoming'
    ON_TRACK = 'on_track'
    DUE_SOON = 'due_soon'
    OVERDUE = 'overdue'
    COMPLETED_ON_TIME = 'completed_on_time'
    COMPLETED_LATE = 'completed_late'
    COMPLETED_TIMING_UNAVAILABLE = 'completed_timing_unavailable'
    INCOMPLETE_TIMING_UNAVAILABLE = 'incomplete_timing_unavailable'
    NO_PHASE = 'no_phase'
    NO_DOJ = 'no_doj'


def calculate_nominal_deadline(date_of_joining, end_day):
    """
    Computes nominal calendar deadline date: DOJ + (end_day - 1 calendar days).
    Day 1 = date_of_joining.
    Returns None if date_of_joining is None or end_day is missing / < 1.
    """
    if not date_of_joining or not end_day or end_day < 1:
        return None
    return date_of_joining + timedelta(days=end_day - 1)


def evaluate_timeline_status(
    start_day,
    end_day,
    date_of_joining,
    is_completed,
    completion_date=None,
    as_of_date=None,
    is_timing_verified=False,
    timing_unavailable_reason=None,
    benchmark_deadline=None
):
    """
    Pure calculation helper evaluating timeline status for a phase or topic.
    ZERO database or ORM queries are executed.

    Parameters:
        start_day: int >= 1 (LearningPhase start_day).
        end_day: int >= start_day (LearningPhase end_day).
        date_of_joining: datetime.date | None (Day 1 anchor).
        is_completed: bool (whether all required work is completed).
        completion_date: datetime.date | None (calendar date of completion).
        as_of_date: datetime.date | None (target evaluation date; defaults to timezone.localdate()).
        is_timing_verified: bool (True ONLY if reliable evidence verifies historical schedule).
        timing_unavailable_reason: str | None (optional pre-existing reason for unverified timing).
        benchmark_deadline: datetime.date | None (optional frozen/authoritative deadline for completed work).

    Returns dict with keys:
        - status: One of PhasePerformanceStatus.*
        - status_label: Human-readable display label
        - nominal_deadline: datetime.date | None (current schedule estimate or benchmark deadline)
        - is_timing_verified: bool
        - days_overdue: int | None (positive days past deadline for verified overdue work)
        - days_late: int | None (fixed positive days past deadline for verified late completion)
        - days_until_due: int | None (days remaining until nominal deadline)
        - is_eligible_for_on_time_rate: bool (True ONLY for verified eligible outcomes)
        - timing_unavailable_reason: str | None
    """
    if as_of_date is None:
        as_of_date = timezone.localdate()

    nominal_deadline = calculate_nominal_deadline(date_of_joining, end_day)
    target_deadline = benchmark_deadline if (is_completed and benchmark_deadline is not None) else nominal_deadline

    # 1. Missing Date of Joining (when no authoritative benchmark deadline exists)
    if date_of_joining is None and not (is_completed and benchmark_deadline is not None):
        status = (
            PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE
            if is_completed
            else PhasePerformanceStatus.NO_DOJ
        )
        label = 'Completed · Timing unavailable' if is_completed else 'DOJ Not Set'
        return {
            'status': status,
            'status_label': label,
            'nominal_deadline': None,
            'is_timing_verified': False,
            'days_overdue': None,
            'days_late': None,
            'days_until_due': None,
            'is_eligible_for_on_time_rate': False,
            'timing_unavailable_reason': 'Learner Date of Joining (DOJ) is not set.',
        }

    # 2. Completed work
    if is_completed:
        # If completion date is missing or timing is not verified, fail conservatively
        if completion_date is None or not is_timing_verified or target_deadline is None:
            reason = timing_unavailable_reason or (
                'Missing completion timestamp.' if completion_date is None
                else 'Historical schedule evidence is absent or unverified.'
            )
            return {
                'status': PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE,
                'status_label': 'Completed · Timing unavailable',
                'nominal_deadline': target_deadline,
                'is_timing_verified': False,
                'days_overdue': None,
                'days_late': None,
                'days_until_due': None,
                'is_eligible_for_on_time_rate': False,
                'timing_unavailable_reason': reason,
            }

        if completion_date <= target_deadline:
            return {
                'status': PhasePerformanceStatus.COMPLETED_ON_TIME,
                'status_label': 'Completed On Time',
                'nominal_deadline': target_deadline,
                'is_timing_verified': True,
                'days_overdue': None,
                'days_late': 0,
                'days_until_due': None,
                'is_eligible_for_on_time_rate': True,
                'timing_unavailable_reason': None,
            }
        else:
            delay = (completion_date - target_deadline).days
            return {
                'status': PhasePerformanceStatus.COMPLETED_LATE,
                'status_label': f'Completed Late (+{delay}d)',
                'nominal_deadline': target_deadline,
                'is_timing_verified': True,
                'days_overdue': None,
                'days_late': delay,
                'days_until_due': None,
                'is_eligible_for_on_time_rate': True,
                'timing_unavailable_reason': None,
            }

    # 3. Incomplete work
    learner_day = (as_of_date - date_of_joining).days + 1

    # Upcoming (phase has not yet started)
    if start_day is not None and learner_day < start_day:
        days_until_due = (nominal_deadline - as_of_date).days if nominal_deadline else None
        return {
            'status': PhasePerformanceStatus.UPCOMING,
            'status_label': 'Upcoming',
            'nominal_deadline': nominal_deadline,
            'is_timing_verified': is_timing_verified,
            'days_overdue': None,
            'days_late': None,
            'days_until_due': days_until_due,
            'is_eligible_for_on_time_rate': False,
            'timing_unavailable_reason': None,
        }

    # Overdue: nominal deadline has passed (today > deadline) and required work remains
    if nominal_deadline and as_of_date > nominal_deadline:
        overdue_days = (as_of_date - nominal_deadline).days
        # An overdue phase may enter the on-time rate denominator ONLY when its deadline is verified
        if is_timing_verified:
            return {
                'status': PhasePerformanceStatus.OVERDUE,
                'status_label': f'Overdue (+{overdue_days}d)',
                'nominal_deadline': nominal_deadline,
                'is_timing_verified': True,
                'days_overdue': overdue_days,
                'days_late': None,
                'days_until_due': 0,
                'is_eligible_for_on_time_rate': True,
                'timing_unavailable_reason': None,
            }
        else:
            return {
                'status': PhasePerformanceStatus.INCOMPLETE_TIMING_UNAVAILABLE,
                'status_label': 'Incomplete · Timing unavailable',
                'nominal_deadline': nominal_deadline,
                'is_timing_verified': False,
                'days_overdue': overdue_days,
                'days_late': None,
                'days_until_due': 0,
                'is_eligible_for_on_time_rate': False,
                'timing_unavailable_reason': timing_unavailable_reason or 'Historical deadline cannot be verified.',
            }

    days_until_due = (nominal_deadline - as_of_date).days if nominal_deadline else None

    # Due Soon: today is the deadline or within the previous two calendar days
    # (deadline - 2 <= today <= deadline), provided the phase has started.
    if nominal_deadline and (nominal_deadline - timedelta(days=2)) <= as_of_date <= nominal_deadline:
        label = 'Due Today' if days_until_due == 0 else f'Due Soon ({days_until_due}d left)'
        return {
            'status': PhasePerformanceStatus.DUE_SOON,
            'status_label': label,
            'nominal_deadline': nominal_deadline,
            'is_timing_verified': is_timing_verified,
            'days_overdue': None,
            'days_late': None,
            'days_until_due': days_until_due,
            'is_eligible_for_on_time_rate': False,  # Deadline has not yet passed; excluded from denominator
            'timing_unavailable_reason': None,
        }

    # On Track: phase has started, work remains, not due soon, not overdue
    return {
        'status': PhasePerformanceStatus.ON_TRACK,
        'status_label': 'On Track',
        'nominal_deadline': nominal_deadline,
        'is_timing_verified': is_timing_verified,
        'days_overdue': None,
        'days_late': None,
        'days_until_due': days_until_due,
        'is_eligible_for_on_time_rate': False,  # Deadline has not yet passed; excluded from denominator
        'timing_unavailable_reason': None,
    }


def calculate_topic_performance(
    topic,
    topic_progress,
    date_of_joining,
    as_of_date=None,
    is_timing_verified=False,
    timing_unavailable_reason=None
):
    """
    Evaluates learning phase performance status for a single topic.
    ZERO database queries are executed.

    Parameters:
        topic: Topic instance.
        topic_progress: TopicProgress instance | None (in-memory).
        date_of_joining: datetime.date | None.
        as_of_date: datetime.date | None (defaults to timezone.localdate()).
        is_timing_verified: bool (caller must establish verification; never assumed True).
        timing_unavailable_reason: str | None.
    """
    if not getattr(topic, 'learning_phase_id', None):
        is_completed = bool(topic_progress and topic_progress.status == TopicProgress.STATUS_COMPLETED)
        return {
            'status': PhasePerformanceStatus.NO_PHASE,
            'status_label': 'Completed (No Phase)' if is_completed else 'No Phase Assigned',
            'nominal_deadline': None,
            'is_timing_verified': False,
            'completed_at': getattr(topic_progress, 'completed_at', None) if topic_progress else None,
            'days_overdue': None,
            'days_late': None,
            'days_until_due': None,
            'is_eligible_for_on_time_rate': False,
            'timing_unavailable_reason': 'Topic is not assigned to a learning phase.',
        }

    phase = topic.learning_phase
    is_completed = bool(topic_progress and topic_progress.status == TopicProgress.STATUS_COMPLETED)
    completed_at = getattr(topic_progress, 'completed_at', None) if topic_progress else None

    completion_date = None
    if completed_at:
        completion_date = timezone.localdate(completed_at) if timezone.is_aware(completed_at) else completed_at.date()

    frozen_deadline = getattr(topic_progress, 'frozen_deadline', None) if topic_progress else None
    if is_completed:
        if frozen_deadline and completed_at:
            is_timing_verified = True
            timing_unavailable_reason = None
        else:
            is_timing_verified = False
            timing_unavailable_reason = 'Topic completion deadline was not recorded.'
    elif not is_completed:
        if date_of_joining is not None:
            is_timing_verified = True
            timing_unavailable_reason = None

    # For incomplete topics, prioritize saved expected_end_day_snapshot over live phase.end_day
    effective_end_day = phase.end_day
    if not is_completed and topic_progress:
        snapshot_end_day = getattr(topic_progress, 'expected_end_day_snapshot', None)
        if snapshot_end_day is not None and snapshot_end_day >= 1:
            effective_end_day = snapshot_end_day

    result = evaluate_timeline_status(
        start_day=phase.start_day,
        end_day=effective_end_day,
        date_of_joining=date_of_joining,
        is_completed=is_completed,
        completion_date=completion_date,
        as_of_date=as_of_date,
        is_timing_verified=is_timing_verified,
        timing_unavailable_reason=timing_unavailable_reason,
        benchmark_deadline=frozen_deadline
    )
    result['completed_at'] = completed_at
    return result


def calculate_phase_performance(
    phase,
    active_topics,
    progress_map,
    date_of_joining,
    as_of_date=None,
    is_timing_verified=None,
    timing_unavailable_reason=None
):
    """
    Evaluates learning phase performance status for a phase across its active published topics.
    ZERO database queries are executed.

    Parameters:
        phase: LearningPhase instance.
        active_topics: Iterable of published, active Topic instances assigned to this phase.
        progress_map: Dict of {topic_id: TopicProgress} for the learner (required in-memory).
        date_of_joining: datetime.date | None.
        as_of_date: datetime.date | None (defaults to timezone.localdate()).
        is_timing_verified: bool | None (defaults to verifying from frozen_deadline/DOJ).
        timing_unavailable_reason: str | None.
    """
    # Filter strictly for active published topics
    topics_list = [
        t for t in active_topics
        if getattr(t, 'is_active', False) and getattr(t, 'status', None) == Topic.STATUS_PUBLISHED
    ]
    total_topics = len(topics_list)

    # Empty phase: zero active published topics. Excluded from completion and rate statistics.
    if total_topics == 0:
        return {
            'status': PhasePerformanceStatus.EMPTY,
            'status_label': 'Empty Phase',
            'is_empty': True,
            'total_topics': 0,
            'completed_topics': 0,
            'nominal_deadline': None,
            'is_timing_verified': False,
            'completed_at': None,
            'days_overdue': None,
            'days_late': None,
            'days_until_due': None,
            'is_eligible_for_on_time_rate': False,
            'timing_unavailable_reason': 'Phase has no active published topics assigned.',
        }

    # Tally completion strictly from progress_map
    completed_count = 0
    completed_entries = []
    has_missing_timestamp = False
    has_missing_frozen_deadline = False

    for t in topics_list:
        prog = progress_map.get(t.id) if progress_map else None
        if prog and prog.status == TopicProgress.STATUS_COMPLETED:
            completed_count += 1
            completed_entries.append((t, prog))
            if not prog.completed_at:
                has_missing_timestamp = True
            if getattr(prog, 'frozen_deadline', None) is None:
                has_missing_frozen_deadline = True

    is_phase_completed = (completed_count == total_topics)

    phase_completed_at = None
    completion_date = None
    benchmark_deadline = None

    if is_phase_completed:
        if completed_entries and not has_missing_timestamp:
            def _trigger_key(entry):
                t, prog = entry
                return (
                    prog.completed_at,
                    getattr(t, 'order', 0) or 0,
                    getattr(t, 'id', 0) or 0,
                )

            triggering_topic, triggering_prog = max(completed_entries, key=_trigger_key)
            phase_completed_at = triggering_prog.completed_at
            completion_date = (
                timezone.localdate(phase_completed_at)
                if timezone.is_aware(phase_completed_at)
                else phase_completed_at.date()
            )
            benchmark_deadline = getattr(triggering_prog, 'frozen_deadline', None)

        if has_missing_timestamp or has_missing_frozen_deadline or (benchmark_deadline is None):
            is_timing_verified = False
            if not timing_unavailable_reason:
                timing_unavailable_reason = 'One or more completed topics lack a recorded deadline or completion timestamp.'
        else:
            if is_timing_verified is None:
                is_timing_verified = True
                timing_unavailable_reason = None
    else:
        if is_timing_verified is None:
            if date_of_joining is not None:
                is_timing_verified = True
                timing_unavailable_reason = None
            else:
                is_timing_verified = False
                timing_unavailable_reason = 'DOJ not set.'

    result = evaluate_timeline_status(
        start_day=phase.start_day,
        end_day=phase.end_day,
        date_of_joining=date_of_joining,
        is_completed=is_phase_completed,
        completion_date=completion_date,
        as_of_date=as_of_date,
        is_timing_verified=is_timing_verified,
        timing_unavailable_reason=timing_unavailable_reason,
        benchmark_deadline=benchmark_deadline
    )
    result['is_empty'] = False
    result['total_topics'] = total_topics
    result['completed_topics'] = completed_count
    result['completed_at'] = phase_completed_at
    return result


# ==============================================================================
# BATCH PROGRESS & LEARNING PHASE PERFORMANCE ENGINE (STAGE 2)
# ==============================================================================

def get_portal_progress_report_data(as_of_date=None):
    """
    Batch-evaluates learner progress, topic-weighted completion, and Learning Phase
    performance metrics across the complete learner cohort.

    ZERO queries inside per-learner, per-subject, per-topic, or per-phase loops.
    All data is preloaded in constant batch queries upfront and evaluated in memory.

    Parameters:
        as_of_date: Optional evaluation date (datetime.date). Defaults to timezone.localdate().

    Returns:
        dict with keys:
            - 'summary': High-level cohort KPIs (unique learner counts & phase record totals).
            - 'learners_data': Evaluated per-learner records for table rendering.
    """
    if as_of_date is None:
        as_of_date = timezone.localdate()

    from django.contrib.contenttypes.models import ContentType
    from accounts.models import User
    from access.models import LearnerAccess
    from access.services import has_subject_access, has_topic_access
    from content.models import LearningPhase

    # 1. Complete Learner Scope (cohort-wide, independent of future UI filters)
    learners = list(User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined'))
    total_learners_count = len(learners)

    empty_summary = {
        'total_learners_count': 0,
        'learners_with_phases_count': 0,
        'learners_no_phases_count': 0,
        'learners_no_doj_count': 0,
        'learner_kpis': {
            'no_doj': 0,
            'no_phase': 0,
            'overdue': 0,
            'due_soon': 0,
            'on_track': 0,
            'upcoming': 0,
            'completed_all': 0,
            'timing_unavailable': 0,
        },
        'phase_kpis': {
            'total_assigned': 0,
            'empty_count': 0,
            'upcoming_count': 0,
            'on_track_count': 0,
            'due_soon_count': 0,
            'overdue_count': 0,
            'completed_on_time_count': 0,
            'completed_late_count': 0,
            'completed_timing_unavailable_count': 0,
            'incomplete_timing_unavailable_count': 0,
            'no_doj_count': 0,
            'verified_denominator': 0,
            'verified_numerator': 0,
            'verified_on_time_rate_percent': None,
            'verified_on_time_rate_display': 'N/A',
        }
    }

    if not learners:
        return {
            'summary': empty_summary,
            'learners_data': []
        }

    learner_ids = [u.id for u in learners]

    # 2. Active Subjects
    active_subjects = list(Subject.objects.filter(is_active=True).order_by('order', 'id'))

    # 3. Learning Phases for Active Subjects
    phases = list(
        LearningPhase.objects.filter(subject__in=active_subjects)
        .select_related('subject')
        .order_by('subject_id', 'start_day', 'id')
    )
    phases_by_subject = {}
    for p in phases:
        phases_by_subject.setdefault(p.subject_id, []).append(p)

    # 4. All Active Topics for Active Subjects (matching get_subject_progress_summary exactly)
    active_topics = list(
        Topic.objects.filter(
            subject__in=active_subjects,
            is_active=True
        )
        .select_related('learning_phase', 'subject')
        .order_by('subject_id', 'order', 'id')
    )
    active_topics_by_subject = {}
    published_topics_by_subject = {}
    published_topics_by_phase = {}
    topic_ids = [t.id for t in active_topics]
    for t in active_topics:
        active_topics_by_subject.setdefault(t.subject_id, []).append(t)
        if t.status == Topic.STATUS_PUBLISHED:
            published_topics_by_subject.setdefault(t.subject_id, []).append(t)
            if t.learning_phase_id:
                published_topics_by_phase.setdefault(t.learning_phase_id, []).append(t)

    # 5. Batch Preload LearnerAccess Explicit Denials
    denies = LearnerAccess.objects.filter(
        learner_id__in=learner_ids,
        is_allowed=False
    ).values_list('learner_id', 'content_type_id', 'object_id')

    cache_by_learner = {u.id: set() for u in learners}
    for l_id, ct_id, obj_id in denies:
        cache_by_learner[l_id].add((ct_id, obj_id))

    for u in learners:
        u._learner_access_cache = cache_by_learner[u.id]

    # 6. Batch Preload TopicProgress Records
    progresses = TopicProgress.objects.filter(
        user_id__in=learner_ids,
        topic_id__in=topic_ids
    ).only(
        'id', 'user_id', 'topic_id', 'status', 'best_score', 'latest_score',
        'attempts_count', 'completed_at', 'expected_end_day_snapshot', 'frozen_deadline',
        'timing_provenance'
    )
    progress_by_learner_topic = {(p.user_id, p.topic_id): p for p in progresses}

    # 7. Cohort Aggregator Data Structures
    cohort_learner_kpis = {
        'no_doj': 0,
        'no_phase': 0,
        'overdue': 0,
        'due_soon': 0,
        'on_track': 0,
        'upcoming': 0,
        'completed_all': 0,
        'timing_unavailable': 0,
    }

    cohort_phase_kpis = {
        'total_assigned': 0,
        'empty_count': 0,
        'upcoming_count': 0,
        'on_track_count': 0,
        'due_soon_count': 0,
        'overdue_count': 0,
        'completed_on_time_count': 0,
        'completed_late_count': 0,
        'completed_timing_unavailable_count': 0,
        'incomplete_timing_unavailable_count': 0,
        'no_doj_count': 0,
        'verified_denominator': 0,
        'verified_numerator': 0,
        'verified_on_time_rate_percent': None,
        'verified_on_time_rate_display': 'N/A',
    }

    learners_with_phases_count = 0
    learners_no_phases_count = 0
    learners_no_doj_count = 0

    learners_data = []

    # 8. Pure In-Memory Per-Learner Evaluation Loop (ZERO ORM queries)
    for learner in learners:
        if learner.date_of_joining is None:
            learners_no_doj_count += 1

        # A. Existing Subject-Average Progress (%): Exact replication of get_overall_learner_progress
        subject_progresses = []
        for subject in active_subjects:
            sub_topics = active_topics_by_subject.get(subject.id, [])
            accessible_sub_topics = [t for t in sub_topics if has_topic_access(learner, t)]
            if not accessible_sub_topics:
                subject_progresses.append(0)
            else:
                comp_count = sum(
                    1 for t in accessible_sub_topics
                    if getattr(progress_by_learner_topic.get((learner.id, t.id)), 'status', None) == TopicProgress.STATUS_COMPLETED
                )
                subject_progresses.append(int(round((comp_count / len(accessible_sub_topics)) * 100)))

        if not active_subjects:
            overall_progress = 0
        else:
            overall_progress = int(round(sum(subject_progresses) / len(active_subjects)))

        # B. Separate Topic-Weighted Progress (%)
        accessible_topics_count = 0
        completed_topics_count = 0
        for subject in active_subjects:
            for t in published_topics_by_subject.get(subject.id, []):
                if has_topic_access(learner, t):
                    accessible_topics_count += 1
                    prog = progress_by_learner_topic.get((learner.id, t.id))
                    if prog and prog.status == TopicProgress.STATUS_COMPLETED:
                        completed_topics_count += 1

        if accessible_topics_count == 0:
            topic_weighted_progress = None
            topic_weighted_display = "N/A"
        else:
            topic_weighted_progress = int(round((completed_topics_count / accessible_topics_count) * 100))
            topic_weighted_display = f"{topic_weighted_progress}%"

        # C. Learning Phase Evaluations for this Learner
        learner_progress_map = {
            t_id: p for (u_id, t_id), p in progress_by_learner_topic.items() if u_id == learner.id
        }

        learner_phases_detail = []
        learner_phase_counts = {
            'completed_on_time': 0,
            'completed_late': 0,
            'completed_timing_unavailable': 0,
            'incomplete_timing_unavailable': 0,
            'overdue': 0,
            'due_soon': 0,
            'on_track': 0,
            'upcoming': 0,
            'no_doj': 0,
            'empty': 0,
            'total_assigned': 0,
        }

        for subject in active_subjects:
            if not has_subject_access(learner, subject):
                continue
            for phase in phases_by_subject.get(subject.id, []):
                phase_topics = [
                    t for t in published_topics_by_phase.get(phase.id, [])
                    if has_topic_access(learner, t)
                ]
                phase_eval = calculate_phase_performance(
                    phase=phase,
                    active_topics=phase_topics,
                    progress_map=learner_progress_map,
                    date_of_joining=learner.date_of_joining,
                    as_of_date=as_of_date,
                )
                phase_eval['phase'] = phase
                phase_eval['subject'] = subject
                learner_phases_detail.append(phase_eval)

                # Tally phase records into learner breakdown & cohort totals
                if phase_eval.get('is_empty'):
                    learner_phase_counts['empty'] += 1
                    cohort_phase_kpis['empty_count'] += 1
                else:
                    learner_phase_counts['total_assigned'] += 1
                    cohort_phase_kpis['total_assigned'] += 1

                    st = phase_eval['status']
                    if st == PhasePerformanceStatus.COMPLETED_ON_TIME:
                        learner_phase_counts['completed_on_time'] += 1
                        cohort_phase_kpis['completed_on_time_count'] += 1
                    elif st == PhasePerformanceStatus.COMPLETED_LATE:
                        learner_phase_counts['completed_late'] += 1
                        cohort_phase_kpis['completed_late_count'] += 1
                    elif st == PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE:
                        learner_phase_counts['completed_timing_unavailable'] += 1
                        cohort_phase_kpis['completed_timing_unavailable_count'] += 1
                    elif st == PhasePerformanceStatus.INCOMPLETE_TIMING_UNAVAILABLE:
                        learner_phase_counts['incomplete_timing_unavailable'] += 1
                        cohort_phase_kpis['incomplete_timing_unavailable_count'] += 1
                    elif st == PhasePerformanceStatus.OVERDUE:
                        learner_phase_counts['overdue'] += 1
                        cohort_phase_kpis['overdue_count'] += 1
                    elif st == PhasePerformanceStatus.DUE_SOON:
                        learner_phase_counts['due_soon'] += 1
                        cohort_phase_kpis['due_soon_count'] += 1
                    elif st == PhasePerformanceStatus.ON_TRACK:
                        learner_phase_counts['on_track'] += 1
                        cohort_phase_kpis['on_track_count'] += 1
                    elif st == PhasePerformanceStatus.UPCOMING:
                        learner_phase_counts['upcoming'] += 1
                        cohort_phase_kpis['upcoming_count'] += 1
                    elif st == PhasePerformanceStatus.NO_DOJ:
                        learner_phase_counts['no_doj'] += 1
                        cohort_phase_kpis['no_doj_count'] += 1

        # D. Mutually Exclusive Learner Primary Status Precedence
        non_empty_phases = [p for p in learner_phases_detail if not p.get('is_empty')]
        if len(non_empty_phases) > 0:
            learners_with_phases_count += 1
        else:
            learners_no_phases_count += 1

        if learner.date_of_joining is None:
            primary_status = 'no_doj'
            primary_status_label = 'DOJ Not Set'
            primary_status_class = 'bg-secondary'
        elif len(non_empty_phases) == 0:
            primary_status = 'no_phase'
            primary_status_label = 'No Phases Assigned'
            primary_status_class = 'bg-secondary'
        elif any(p['status'] == PhasePerformanceStatus.OVERDUE for p in non_empty_phases):
            primary_status = 'overdue'
            primary_status_label = 'Overdue'
            primary_status_class = 'bg-danger'
        elif any(p['status'] == PhasePerformanceStatus.DUE_SOON for p in non_empty_phases):
            primary_status = 'due_soon'
            primary_status_label = 'Due Soon'
            primary_status_class = 'bg-warning text-dark'
        elif any(p['status'] == PhasePerformanceStatus.ON_TRACK for p in non_empty_phases):
            primary_status = 'on_track'
            primary_status_label = 'On Track'
            primary_status_class = 'bg-info text-dark'
        elif all(p['status'] in (
            PhasePerformanceStatus.COMPLETED_ON_TIME,
            PhasePerformanceStatus.COMPLETED_LATE,
            PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE
        ) for p in non_empty_phases):
            primary_status = 'completed_all'
            primary_status_label = 'Completed'
            primary_status_class = 'bg-success'
        elif any(p['status'] in (
            PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE,
            PhasePerformanceStatus.INCOMPLETE_TIMING_UNAVAILABLE
        ) for p in non_empty_phases):
            primary_status = 'timing_unavailable'
            primary_status_label = 'Timing Unavailable'
            primary_status_class = 'bg-light text-dark border'
        elif all(p['status'] in (
            PhasePerformanceStatus.COMPLETED_ON_TIME,
            PhasePerformanceStatus.COMPLETED_LATE,
            PhasePerformanceStatus.UPCOMING
        ) for p in non_empty_phases):
            primary_status = 'upcoming'
            primary_status_label = 'Upcoming'
            primary_status_class = 'bg-secondary'
        else:
            primary_status = 'timing_unavailable'
            primary_status_label = 'Timing Unavailable'
            primary_status_class = 'bg-light text-dark border'

        cohort_learner_kpis[primary_status] += 1

        learners_data.append({
            'learner': learner,
            'date_of_joining': learner.date_of_joining,
            'overall_progress': overall_progress,
            'topic_weighted_progress': topic_weighted_progress,
            'topic_weighted_display': topic_weighted_display,
            'accessible_topics_count': accessible_topics_count,
            'completed_topics_count': completed_topics_count,
            'primary_status': primary_status,
            'primary_status_label': primary_status_label,
            'primary_status_class': primary_status_class,
            'phase_counts': learner_phase_counts,
            'phases_detail': learner_phases_detail,
        })

    # 9. Cohort-Wide Verified On-Time Rate Metric
    verified_den = (
        cohort_phase_kpis['completed_on_time_count'] +
        cohort_phase_kpis['completed_late_count'] +
        cohort_phase_kpis['overdue_count']
    )
    verified_num = cohort_phase_kpis['completed_on_time_count']

    if verified_den > 0:
        rate_pct = int(round((verified_num / verified_den) * 100))
        rate_disp = f"{rate_pct}%"
    else:
        rate_pct = None
        rate_disp = "N/A"

    cohort_phase_kpis['verified_denominator'] = verified_den
    cohort_phase_kpis['verified_numerator'] = verified_num
    cohort_phase_kpis['verified_on_time_rate_percent'] = rate_pct
    cohort_phase_kpis['verified_on_time_rate_display'] = rate_disp

    summary = {
        'total_learners_count': total_learners_count,
        'learners_with_phases_count': learners_with_phases_count,
        'learners_no_phases_count': learners_no_phases_count,
        'learners_no_doj_count': learners_no_doj_count,
        'learner_kpis': cohort_learner_kpis,
        'phase_kpis': cohort_phase_kpis,
    }

    return {
        'summary': summary,
        'learners_data': learners_data,
    }


# ==============================================================================
# HISTORICAL TIMING PROVENANCE AUDIT TRAIL SERVICES
# ==============================================================================

class StaleRollbackError(ValueError):
    """Raised when attempting to rollback an audit entry whose post-audit state has diverged or been rolled back."""
    pass


def apply_timing_reconstruction_with_audit(
    topic_progress,
    new_frozen_deadline,
    new_expected_end_day_snapshot=None,
    new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED,
    new_status=None,
    new_completed_at=None,
    repair_batch_id=None,
    action=None,
    reason="Historical timing reconstruction under approved policy",
    actor=None,
):
    """
    Applies a historical timing reconstruction or manual correction to a TopicProgress record,
    atomically creating an append-only TopicProgressTimingAudit entry in the same transaction.

    Guarantees:
    - Atomicity: TopicProgress update and audit entry creation occur in the same database transaction.
    - Idempotency: If before and after values are identical (no field actually changed),
      no update is performed and NO audit entry is created.
    - Before and after values capture actual database values.
    """
    from progress.models import TopicProgressTimingAudit
    import uuid

    if action is None:
        action = TopicProgressTimingAudit.ACTION_HISTORICAL_RECONSTRUCTION

    if repair_batch_id is None:
        repair_batch_id = uuid.uuid4()

    with transaction.atomic():
        tp_id = topic_progress if isinstance(topic_progress, (int, str)) else topic_progress.id
        tp = TopicProgress.objects.select_for_update().get(id=int(tp_id))

        before_expected_end_day_snapshot = tp.expected_end_day_snapshot
        before_frozen_deadline = tp.frozen_deadline
        before_timing_provenance = tp.timing_provenance
        before_status = tp.status
        before_completed_at = tp.completed_at

        target_expected_end_day_snapshot = (
            new_expected_end_day_snapshot
            if new_expected_end_day_snapshot is not None
            else before_expected_end_day_snapshot
        )
        target_frozen_deadline = new_frozen_deadline
        target_timing_provenance = new_timing_provenance
        target_status = new_status if new_status is not None else before_status
        target_completed_at = new_completed_at if new_completed_at is not None else before_completed_at

        # Check if anything actually changes
        has_changes = (
            before_expected_end_day_snapshot != target_expected_end_day_snapshot
            or before_frozen_deadline != target_frozen_deadline
            or before_timing_provenance != target_timing_provenance
            or before_status != target_status
            or before_completed_at != target_completed_at
        )

        if not has_changes:
            return None, False

        # Apply update
        tp.expected_end_day_snapshot = target_expected_end_day_snapshot
        tp.frozen_deadline = target_frozen_deadline
        tp.timing_provenance = target_timing_provenance
        tp.status = target_status
        tp.completed_at = target_completed_at

        update_fields = [
            'expected_end_day_snapshot',
            'frozen_deadline',
            'timing_provenance',
            'status',
            'completed_at',
        ]
        tp.save(update_fields=update_fields)

        # Create audit entry
        audit_entry = TopicProgressTimingAudit.objects.create(
            topic_progress=tp,
            original_topic_progress_id=tp.id,
            repair_batch_id=repair_batch_id,
            action=action,
            reason=reason or "Historical timing reconstruction under approved policy",
            before_expected_end_day_snapshot=before_expected_end_day_snapshot,
            after_expected_end_day_snapshot=target_expected_end_day_snapshot,
            before_frozen_deadline=before_frozen_deadline,
            after_frozen_deadline=target_frozen_deadline,
            before_timing_provenance=before_timing_provenance,
            after_timing_provenance=target_timing_provenance,
            before_status=before_status,
            after_status=target_status,
            before_completed_at=before_completed_at,
            after_completed_at=target_completed_at,
            actor=actor,
        )

        return audit_entry, True


def rollback_timing_reconstruction_with_audit(
    audit_entry,
    reason,
    actor=None,
    rollback_batch_id=None,
):
    """
    Rolls back the changes recorded in a TopicProgressTimingAudit entry.
    Restores the TopicProgress record to the 'before' values of the given audit entry,
    and creates a NEW audit entry with action='rollback' and explicit link reverted_audit_entry.

    Guarantees:
    - Safe Rollback Validation: Verifies all five fields on the current TopicProgress row match
      audit_entry.after_* values. If even one field has diverged, raises StaleRollbackError without modifying.
    - Duplicate Rollback Prevention: Validates that audit_entry has not already been rolled back.
      Enforced both via application check and database UNIQUE constraint on reverted_audit_entry.
    - Non-Rollback of Rollbacks: Rejects rolling back a rollback entry.
    - Never modifies or deletes the original audit entry (append-only).
    """
    from progress.models import TopicProgressTimingAudit
    import uuid

    if rollback_batch_id is None:
        rollback_batch_id = uuid.uuid4()

    with transaction.atomic():
        # 1. Lock the audit entry itself to serialize concurrent rollback attempts
        locked_audit = TopicProgressTimingAudit.objects.select_for_update().get(id=audit_entry.id)

        # 2. Check if the audit entry is itself a rollback entry
        if locked_audit.action == TopicProgressTimingAudit.ACTION_ROLLBACK:
            raise StaleRollbackError(f"Audit entry #{locked_audit.id} is a rollback entry and cannot be rolled back.")

        # 3. Check if this audit entry has already been rolled back
        existing_rollback = TopicProgressTimingAudit.objects.filter(reverted_audit_entry=locked_audit).first()
        if existing_rollback:
            raise StaleRollbackError(
                f"Audit entry #{locked_audit.id} has already been rolled back by audit entry #{existing_rollback.id}."
            )

        tp_id = locked_audit.original_topic_progress_id or locked_audit.topic_progress_id
        if not tp_id:
            raise ValueError(f"Cannot rollback audit entry #{locked_audit.id} without a valid TopicProgress ID.")

        # 4. Lock the TopicProgress row
        tp = TopicProgress.objects.select_for_update().get(id=tp_id)

        # 5. Compare ALL five current values against locked_audit.after_* values
        mismatches = []
        if tp.expected_end_day_snapshot != locked_audit.after_expected_end_day_snapshot:
            mismatches.append(
                f"expected_end_day_snapshot (current: {tp.expected_end_day_snapshot}, expected: {locked_audit.after_expected_end_day_snapshot})"
            )
        if tp.frozen_deadline != locked_audit.after_frozen_deadline:
            mismatches.append(
                f"frozen_deadline (current: {tp.frozen_deadline}, expected: {locked_audit.after_frozen_deadline})"
            )
        if tp.timing_provenance != locked_audit.after_timing_provenance:
            mismatches.append(
                f"timing_provenance (current: {tp.timing_provenance}, expected: {locked_audit.after_timing_provenance})"
            )
        if tp.status != locked_audit.after_status:
            mismatches.append(
                f"status (current: {tp.status}, expected: {locked_audit.after_status})"
            )
        if tp.completed_at != locked_audit.after_completed_at:
            mismatches.append(
                f"completed_at (current: {tp.completed_at}, expected: {locked_audit.after_completed_at})"
            )

        if mismatches:
            raise StaleRollbackError(
                f"Cannot rollback audit entry #{locked_audit.id} on TopicProgress #{tp.id}: "
                f"current database values have diverged from post-audit state. "
                f"Mismatches: {', '.join(mismatches)}."
            )

        # 6. Apply restore to before_* values
        current_expected_end_day_snapshot = tp.expected_end_day_snapshot
        current_frozen_deadline = tp.frozen_deadline
        current_timing_provenance = tp.timing_provenance
        current_status = tp.status
        current_completed_at = tp.completed_at

        restore_snapshot = locked_audit.before_expected_end_day_snapshot
        restore_deadline = locked_audit.before_frozen_deadline
        restore_provenance = locked_audit.before_timing_provenance
        restore_status = locked_audit.before_status if locked_audit.before_status else tp.status
        restore_completed_at = locked_audit.before_completed_at

        tp.expected_end_day_snapshot = restore_snapshot
        tp.frozen_deadline = restore_deadline
        tp.timing_provenance = restore_provenance
        tp.status = restore_status
        tp.completed_at = restore_completed_at

        update_fields = [
            'expected_end_day_snapshot',
            'frozen_deadline',
            'timing_provenance',
            'status',
            'completed_at',
        ]
        tp.save(update_fields=update_fields)

        # 7. Create separate rollback audit entry explicitly linked to locked_audit
        rollback_entry = TopicProgressTimingAudit.objects.create(
            topic_progress=tp,
            original_topic_progress_id=tp.id,
            reverted_audit_entry=locked_audit,
            repair_batch_id=rollback_batch_id,
            action=TopicProgressTimingAudit.ACTION_ROLLBACK,
            reason=reason or f"Rollback of audit entry #{locked_audit.id}",
            before_expected_end_day_snapshot=current_expected_end_day_snapshot,
            after_expected_end_day_snapshot=restore_snapshot,
            before_frozen_deadline=current_frozen_deadline,
            after_frozen_deadline=restore_deadline,
            before_timing_provenance=current_timing_provenance,
            after_timing_provenance=restore_provenance,
            before_status=current_status,
            after_status=restore_status,
            before_completed_at=current_completed_at,
            after_completed_at=restore_completed_at,
            actor=actor,
        )

        return rollback_entry


def apply_timing_reconstruction_batch(
    plans,
    repair_batch_id=None,
    common_reason="Historical timing reconstruction under approved policy",
    actor=None,
):
    """
    Executes a cohort or multi-record timing reconstruction batch within a single outer
    transaction.atomic() block, guaranteeing all-or-nothing atomicity.

    If any single record update or audit insertion fails:
    - All TopicProgress updates in this batch roll back.
    - All audit entries created in this batch roll back.
    - No partial batch remains committed.

    Each item in `plans` should be a dict or mapping containing:
    - topic_progress: TopicProgress instance or ID
    - new_frozen_deadline: Date
    - new_expected_end_day_snapshot: int (optional)
    - new_timing_provenance: str (optional, defaults to retrospectively_reconstructed)
    - new_status: str (optional)
    - new_completed_at: datetime (optional)
    - reason: str (optional, defaults to common_reason)
    """
    import uuid

    if repair_batch_id is None:
        repair_batch_id = uuid.uuid4()

    created_audits = []

    with transaction.atomic():
        for plan in plans:
            tp = plan['topic_progress']
            audit_entry, changed = apply_timing_reconstruction_with_audit(
                topic_progress=tp,
                new_frozen_deadline=plan['new_frozen_deadline'],
                new_expected_end_day_snapshot=plan.get('new_expected_end_day_snapshot'),
                new_timing_provenance=plan.get('new_timing_provenance', TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED),
                new_status=plan.get('new_status'),
                new_completed_at=plan.get('new_completed_at'),
                repair_batch_id=repair_batch_id,
                action=plan.get('action'),
                reason=plan.get('reason', common_reason),
                actor=actor,
            )
            if changed and audit_entry:
                created_audits.append(audit_entry)

    return created_audits, repair_batch_id
