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
    
    review_data = request.session.get(f'quiz_review_{attempt.id}')
    
    questions = list(topic.questions.prefetch_related('choices').all())
    question_map = {q.id: q for q in questions}
    
    question_reviews = []
    limitation_message = None
    
    if review_data:
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
            "Detailed per-question breakdown is only available immediately after submission "
            "because individual response choices are not stored in historical attempt logs."
        )
    
    wrong_count = attempt.total_questions - attempt.correct_count
    
    next_topic = Topic.objects.filter(
        subject=topic.subject,
        is_active=True,
        order__gt=topic.order
    ).first()
    
    context = {
        'subject': topic.subject,
        'topic': topic,
        'attempt': attempt,
        'wrong_count': wrong_count,
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

