from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from content.models import Topic
from progress.services import submit_quiz_attempt, get_topic_display_status
from .models import Question, Choice, QuizAttempt

@login_required
def quiz_modal(request, slug, topic_id):
    topic = get_object_or_404(Topic, id=topic_id, subject__slug=slug, is_active=True)
    if not topic.is_assessment_ready():
        return HttpResponseBadRequest("Quiz is not assessment-ready.")
        
    questions = list(topic.questions.prefetch_related('choices').all())
    
    if request.method == 'POST':
        attempt, progress = submit_quiz_attempt(request.user, topic, request.POST)
        
        # Build question review details for current submission
        review_data = []
        for q in questions:
            user_choice_id = request.POST.get(str(q.id)) or request.POST.get(q.id)
            user_choice = None
            if user_choice_id:
                try:
                    user_choice_id = int(user_choice_id)
                    user_choice = next((c for c in q.choices.all() if c.id == user_choice_id), None)
                except (ValueError, TypeError):
                    pass
            correct_choice = next((c for c in q.choices.all() if c.is_correct), None)
            is_correct = bool(user_choice and correct_choice and user_choice.id == correct_choice.id)
            
            review_data.append({
                'question_id': q.id,
                'question_text': q.text,
                'user_choice_id': user_choice.id if user_choice else None,
                'user_choice_text': user_choice.text if user_choice else 'No answer selected',
                'correct_choice_id': correct_choice.id if correct_choice else None,
                'correct_choice_text': correct_choice.text if correct_choice else 'N/A',
                'is_correct': is_correct,
            })
            
        request.session[f'quiz_review_{attempt.id}'] = review_data
        
        return redirect('quiz_result', slug=slug, topic_id=topic.id, attempt_id=attempt.id)
    
    context = {
        'subject': topic.subject,
        'topic': topic,
        'questions': questions,
        'effective_passing_score': topic.effective_passing_score,
    }
    return render(request, 'learner/quiz_modal.html', context)

@login_required
def quiz_result(request, slug, topic_id, attempt_id):
    topic = get_object_or_404(Topic, id=topic_id, subject__slug=slug, is_active=True)
    attempt = get_object_or_404(QuizAttempt, id=attempt_id, user=request.user, topic=topic)
    
    responses = list(attempt.responses.select_related('question').prefetch_related('selected_choices', 'question__choices').all())
    question_reviews = []
    limitation_message = None

    # Initialize aggregate counters
    correct_count = 0
    wrong_count = 0
    requires_review_count = 0

    if responses:
        for idx, resp in enumerate(responses, 1):
            q = resp.question
            user_choices = list(resp.selected_choices.all())
            correct_choices = [c for c in q.choices.all() if c.is_correct]

            # Prepare display texts based on question type
            if q.question_type in (Question.TYPE_SINGLE_CHOICE, Question.TYPE_TRUE_FALSE):
                user_choice_text = user_choices[0].text if user_choices else 'No answer selected'
                correct_choice_text = correct_choices[0].text if correct_choices else 'N/A'
            elif q.question_type == Question.TYPE_MULTIPLE_CHOICE:
                user_choice_text = ", ".join([c.text for c in user_choices]) if user_choices else 'No answer selected'
                correct_choice_text = ", ".join([c.text for c in correct_choices]) if correct_choices else 'N/A'
            elif q.question_type == Question.TYPE_SHORT_ANSWER:
                user_choice_text = resp.text_response if resp.text_response else 'No answer entered'
                accepted = q.get_accepted_answers_list()
                correct_choice_text = ", ".join(accepted) if accepted else 'N/A'
            elif q.question_type == Question.TYPE_PARAGRAPH:
                user_choice_text = resp.text_response if resp.text_response else 'No response entered'
                correct_choice_text = 'Requires review / Not automatically graded'

            # Update aggregate counters based on grading state
            if resp.is_correct is True:
                correct_count += 1
            elif resp.is_correct is False:
                wrong_count += 1
            else:
                requires_review_count += 1

            question_reviews.append({
                'number': idx,
                'question_id': q.id,
                'question_type': q.question_type,
                'question_text': q.text,
                'user_choice_text': user_choice_text,
                'correct_choice_text': correct_choice_text,
                'is_correct': resp.is_correct,
            })
    else:
        # Legacy fallback using session data (unchanged)
        review_data = request.session.get(f'quiz_review_{attempt.id}')
        if review_data:
            questions = list(topic.questions.prefetch_related('choices').all())
            question_map = {q.id: q for q in questions}
            for idx, item in enumerate(review_data, 1):
                q_id = item.get('question_id')
                q_obj = question_map.get(q_id)
                question_reviews.append({
                    'number': idx,
                    'question_text': item.get('question_text') or (q_obj.text if q_obj else f'Question #{idx}'),
                    'user_choice_text': item.get('user_choice_text'),
                    'correct_choice_text': item.get('correct_choice_text'),
                    'is_correct': item.get('is_correct'),
                })
        else:
            limitation_message = (
                "Detailed per-question breakdown is only available immediately after submission for legacy attempts "
                "because individual response choices were not stored in historical attempt logs."
            )

    # Derive gradable question count (excluding requires‑review)
    gradable_questions = correct_count + wrong_count
    has_gradable = gradable_questions > 0

    next_topic = Topic.objects.filter(
        subject=topic.subject,
        is_active=True,
        order__gt=topic.order
    ).first()

    context = {
        'subject': topic.subject,
        'topic': topic,
        'attempt': attempt,
        'correct_count': correct_count,
        'wrong_count': wrong_count,
        'requires_review_count': requires_review_count,
        'gradable_questions': gradable_questions,
        'has_gradable': has_gradable,
        'question_reviews': question_reviews,
        'limitation_message': limitation_message,
        'next_topic': next_topic,
        'effective_passing_score': attempt.passing_score_used,
    }
    return render(request, 'learner/quiz_result_partial.html', context)

@login_required
def quiz_submit(request, topic_id):
    if request.method != 'POST':
        return HttpResponseBadRequest("POST required")
        
    topic = get_object_or_404(Topic, id=topic_id, is_active=True)
    if not topic.is_assessment_ready():
        return HttpResponseBadRequest("Quiz is not assessment-ready.")
        
    attempt, progress = submit_quiz_attempt(request.user, topic, request.POST)
    return redirect('quiz_result', slug=topic.subject.slug, topic_id=topic.id, attempt_id=attempt.id)

