from django.shortcuts import render, get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.contrib import messages
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.db import transaction
from django.db.models import ProtectedError
import secrets
import string

from content.models import Subject, Topic, Video, Resource, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from accounts.models import User
from progress.models import TopicProgress
from progress.services import (
    get_subject_progress_summary,
    get_overall_learner_progress,
    get_topic_display_status,
    recalculate_attempt_score,
)
from .decorators import admin_required
from access.models import LearnerAccess
from access import services as access_services
from .forms import (
    SubjectForm,
    TopicForm,
    TopicAddForm,
    VideoForm,
    ResourceForm,
    QuestionForm,
    LearnerForm,
    LearnerPasswordResetForm,
    LearnerEditForm,
    SiteConfigForm,
    LearnerAccessForm,
)


# --- SUBJECTS MANAGEMENT ---

@admin_required
def portal_subjects_list(request):
    subjects = Subject.objects.all().order_by('order', 'name')
    form = SubjectForm()
    
    context = {
        'subjects': subjects,
        'form': form,
    }
    return render(request, 'portal/subjects_list.html', context)

@admin_required
def portal_subject_add(request):
    if request.method == 'POST':
        form = SubjectForm(request.POST)
        if form.is_valid():
            subject = form.save()
            messages.success(request, f"Subject '{subject.name}' created successfully.")
            return redirect('portal_subjects_list')
        # Failure: render list with bound form and modal flag
        subjects = Subject.objects.all().order_by('order', 'name')
        context = {
            'subjects': subjects,
            'form': form,
            'show_modal': 'subject_add',
        }
        return render(request, 'portal/subjects_list.html', context)
    # Non‑POST fallback
    return redirect('portal_subjects_list')

@admin_required
def portal_subject_edit(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    if request.method == 'POST':
        form = SubjectForm(request.POST, instance=subject)
        if form.is_valid():
            form.save()
            messages.success(request, f"Subject '{subject.name}' updated successfully.")
            return redirect('portal_subjects_list')
        # Failure: render list with bound form and modal flag
        subjects = Subject.objects.all().order_by('order', 'name')
        context = {
            'subjects': subjects,
            'form': form,
            'show_modal': f'subject_edit_{subject.id}',
        }
        return render(request, 'portal/subjects_list.html', context)
    return redirect('portal_subjects_list')

@admin_required
def portal_subject_archive(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    if request.method == 'POST':
        subject.is_active = not subject.is_active
        subject.save()
        status_str = "activated" if subject.is_active else "archived"
        messages.success(request, f"Subject '{subject.name}' has been {status_str}.")
    return redirect('portal_subjects_list')

@admin_required
def portal_subjects_reorder(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            subject_ids = data.get('subject_ids', [])
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format.'}, status=400)
            
        if not subject_ids:
            return JsonResponse({'status': 'error', 'message': 'No subject IDs provided.'}, status=400)
            
        try:
            subject_ids = [int(sid) for sid in subject_ids]
        except ValueError:
            return JsonResponse({'status': 'error', 'message': 'Invalid subject IDs format.'}, status=400)
            
        with transaction.atomic():
            current_subjects = list(Subject.objects.values_list('id', flat=True))
            
            if len(subject_ids) != len(current_subjects):
                return JsonResponse({'status': 'error', 'message': 'Submitted list length does not match existing subjects.'}, status=400)
                
            if set(subject_ids) != set(current_subjects):
                return JsonResponse({'status': 'error', 'message': 'Submitted IDs do not exactly match existing subjects.'}, status=400)
                
            if len(subject_ids) != len(set(subject_ids)):
                return JsonResponse({'status': 'error', 'message': 'Duplicate IDs found in submission.'}, status=400)
                
            for index, sid in enumerate(subject_ids, start=1):
                Subject.objects.filter(id=sid).update(order=index)
                
        return JsonResponse({'status': 'ok', 'message': 'Subject order saved successfully.'})
    return HttpResponseBadRequest("POST required")

# --- TOPICS MANAGEMENT ---

@admin_required
def portal_topics_list(request, subject_id=None):
    if subject_id:
        selected_subject = get_object_or_404(Subject, id=subject_id)
        topics = Topic.objects.filter(subject=selected_subject).order_by('order', 'id')
    else:
        selected_subject = None
        topics = Topic.objects.select_related('subject').all().order_by('subject__order', 'order', 'id')
        
    subjects = Subject.objects.filter(is_active=True)
    form = TopicAddForm(initial={'subject': selected_subject} if selected_subject else {})

    context = {
        'topics': topics,
        'subjects': subjects,
        'selected_subject': selected_subject,
        'form': form,
    }
    return render(request, 'portal/topics_list.html', context)

@admin_required
def portal_topic_add(request):
    if request.method == 'POST':
        form = TopicAddForm(request.POST)
        if form.is_valid():
            topic = form.save(commit=False)
            # Force Draft status on creation
            topic.status = Topic.STATUS_DRAFT
            # is_active will be synced in Topic.save()
            topic.save()
            messages.success(request, f"Topic '{topic.name}' created as Draft. Now add videos, materials, and questions.")
            return redirect('portal_topic_edit', topic_id=topic.id)
        # Failure: render topics list with bound form and modal flag
        selected_subject = None
        topics = Topic.objects.select_related('subject').all().order_by('subject__order', 'order', 'id')
        subjects = Subject.objects.filter(is_active=True)
        context = {
            'topics': topics,
            'subjects': subjects,
            'selected_subject': selected_subject,
            'form': form,
            'show_modal': 'topic_add',
        }
        return render(request, 'portal/topics_list.html', context)
    return redirect('portal_topics_list')

@admin_required
def portal_topics_reorder(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            subject_id = data.get('subject_id')
            topic_ids = data.get('topic_ids', [])
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format.'}, status=400)

        if not subject_id:
            return JsonResponse({'status': 'error', 'message': 'Subject ID is required.'}, status=400)
        
        if not topic_ids:
            return JsonResponse({'status': 'error', 'message': 'No topic IDs provided.'}, status=400)
            
        try:
            subject_id = int(subject_id)
            topic_ids = [int(tid) for tid in topic_ids]
        except ValueError:
            return JsonResponse({'status': 'error', 'message': 'Invalid ID format.'}, status=400)
            
        if not Subject.objects.filter(id=subject_id).exists():
            return JsonResponse({'status': 'error', 'message': 'Subject does not exist.'}, status=400)

        with transaction.atomic():
            current_topics = list(Topic.objects.filter(subject_id=subject_id).values_list('id', flat=True))
            
            if len(topic_ids) != len(current_topics):
                return JsonResponse({'status': 'error', 'message': 'Submitted list length does not match existing topics.'}, status=400)
                
            if set(topic_ids) != set(current_topics):
                return JsonResponse({'status': 'error', 'message': 'Submitted IDs do not exactly match existing topics for this subject.'}, status=400)
                
            if len(topic_ids) != len(set(topic_ids)):
                return JsonResponse({'status': 'error', 'message': 'Duplicate IDs found in submission.'}, status=400)
                
            for index, tid in enumerate(topic_ids, start=1):
                Topic.objects.filter(id=tid).update(order=index)
                
        return JsonResponse({'status': 'ok', 'message': 'Topic order saved successfully.'})
    return HttpResponseBadRequest("POST required")

# --- TOPIC CENTRAL EDITOR & PUBLISH GATE ---

@admin_required
def portal_topic_edit(request, topic_id):
    topic = get_object_or_404(Topic, id=topic_id)
    site_config = SiteConfig.get_solo()
    required_count = site_config.default_required_question_count
    current_question_count = topic.questions.count()
    
    if request.method == 'POST':
        form = TopicForm(request.POST, instance=topic)
        if form.is_valid():
            target_status = form.cleaned_data.get('status')
            target_assessment_required = form.cleaned_data.get('assessment_required', True)
            # PUBLISH GATE CHECK
            if target_status == Topic.STATUS_PUBLISHED and target_assessment_required and not topic.is_assessment_ready():
                messages.error(request, "Topic was not published.")
                # Render with modal open, no save
                videos = topic.videos.all().order_by('order', 'id')
                resources = topic.resources.all().order_by('order', 'id')
                questions = topic.questions.all().order_by('order', 'id')
                video_form = VideoForm()
                resource_form = ResourceForm()
                context = {
                    'topic': topic,
                    'form': form,
                    'videos': videos,
                    'resources': resources,
                    'questions': questions,
                    'video_form': video_form,
                    'resource_form': resource_form,
                    'required_question_count': required_count,
                    'current_question_count': current_question_count,
                    'is_publishable': current_question_count >= required_count,
                }
                return render(request, 'portal/topic_edit.html', context)
            # Save normally (draft or allowed publish)
            updated_topic = form.save()
            messages.success(request, f"Topic '{updated_topic.name}' saved successfully.")
            return redirect('portal_topic_edit', topic_id=topic.id)
        else:
            # Form invalid: render with bound form and errors on the page
            videos = topic.videos.all().order_by('order', 'id')
            resources = topic.resources.all().order_by('order', 'id')
            questions = topic.questions.all().order_by('order', 'id')
            video_form = VideoForm()
            resource_form = ResourceForm()
            context = {
                'topic': topic,
                'form': form,
                'videos': videos,
                'resources': resources,
                'questions': questions,
                'video_form': video_form,
                'resource_form': resource_form,
                'required_question_count': required_count,
                'current_question_count': current_question_count,
                'is_publishable': topic.is_assessment_ready(),
            }
            return render(request, 'portal/topic_edit.html', context)
    else:
        form = TopicForm(instance=topic)

    videos = topic.videos.all().order_by('order', 'id')
    resources = topic.resources.all().order_by('order', 'id')
    questions = topic.questions.all().order_by('order', 'id')
    
    video_form = VideoForm()
    resource_form = ResourceForm()

    context = {
        'topic': topic,
        'form': form,
        'videos': videos,
        'resources': resources,
        'questions': questions,
        'video_form': video_form,
        'resource_form': resource_form,
        'required_question_count': required_count,
        'current_question_count': current_question_count,
        'is_publishable': topic.is_assessment_ready(),
    }
    return render(request, 'portal/topic_edit.html', context)

# --- INLINE VIDEOS & MATERIALS MANAGEMENT ---

@admin_required
def portal_video_add(request, topic_id):
    topic = get_object_or_404(Topic, id=topic_id)
    if request.method == 'POST':
        form = VideoForm(request.POST)
        if form.is_valid():
            video = form.save(commit=False)
            video.topic = topic
            video.save()
            messages.success(request, f"Video tutorial '{video.title}' added successfully.")
            return redirect('portal_topic_edit', topic_id=topic.id)
        else:
            site_config = SiteConfig.get_solo()
            context = {'topic': topic, 'form': TopicForm(instance=topic), 'videos': topic.videos.all().order_by('order', 'id'), 'resources': topic.resources.all().order_by('order', 'id'), 'questions': topic.questions.all().order_by('order', 'id'), 'video_form': form, 'resource_form': ResourceForm(), 'required_question_count': site_config.default_required_question_count, 'current_question_count': topic.questions.count(), 'is_publishable': topic.is_assessment_ready(), 'show_modal': 'video_add'}
            return render(request, 'portal/topic_edit.html', context)

@admin_required
def portal_video_delete(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    topic_id = video.topic_id
    if request.method == 'POST':
        video.delete()
        messages.success(request, "Video removed successfully.")
    return redirect('portal_topic_edit', topic_id=topic_id)

@admin_required
def portal_material_add(request, topic_id):
    topic = get_object_or_404(Topic, id=topic_id)
    if request.method == 'POST':
        form = ResourceForm(request.POST)
        if form.is_valid():
            material = form.save(commit=False)
            material.topic = topic
            material.save()
            messages.success(request, f"Learning material '{material.title}' added successfully.")
            return redirect('portal_topic_edit', topic_id=topic.id)
        else:
            site_config = SiteConfig.get_solo()
            context = {'topic': topic, 'form': TopicForm(instance=topic), 'videos': topic.videos.all().order_by('order', 'id'), 'resources': topic.resources.all().order_by('order', 'id'), 'questions': topic.questions.all().order_by('order', 'id'), 'video_form': VideoForm(), 'resource_form': form, 'required_question_count': site_config.default_required_question_count, 'current_question_count': topic.questions.count(), 'is_publishable': topic.is_assessment_ready(), 'show_modal': 'material_add'}
            return render(request, 'portal/topic_edit.html', context)

@admin_required
def portal_material_delete(request, material_id):
    material = get_object_or_404(Resource, id=material_id)
    topic_id = material.topic_id
    if request.method == 'POST':
        material.delete()
        messages.success(request, "Learning material removed successfully.")
    return redirect('portal_topic_edit', topic_id=topic_id)

@admin_required
def portal_video_edit(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    if request.method == 'POST':
        form = VideoForm(request.POST, instance=video)
        if form.is_valid():
            form.save()
            messages.success(request, f"Video '{video.title}' updated successfully.")
            return redirect('portal_topic_edit', topic_id=video.topic_id)
        # Failure: render topic edit with bound form and modal flag
        topic = video.topic
        videos = topic.videos.all().order_by('order', 'id')
        resources = topic.resources.all().order_by('order', 'id')
        questions = topic.questions.all().order_by('order', 'id')
        video_form = form
        resource_form = ResourceForm()
        context = {
            'topic': topic,
            'videos': videos,
            'resources': resources,
            'questions': questions,
            'video_form': video_form,
            'resource_form': resource_form,
            'show_modal': f'video_edit_{video.id}',
        }
        return render(request, 'portal/topic_edit.html', context)
    else:
        return redirect('portal_topic_edit', topic_id=video.topic_id)

@admin_required
def portal_material_edit(request, material_id):
    material = get_object_or_404(Resource, id=material_id)
    if request.method == 'POST':
        form = ResourceForm(request.POST, instance=material)
        if form.is_valid():
            form.save()
            messages.success(request, f"Material '{material.title}' updated successfully.")
            return redirect('portal_topic_edit', topic_id=material.topic_id)
        # Failure: render topic edit with bound resource form and modal flag
        topic = material.topic
        videos = topic.videos.all().order_by('order', 'id')
        resources = topic.resources.all().order_by('order', 'id')
        questions = topic.questions.all().order_by('order', 'id')
        video_form = VideoForm()
        resource_form = form
        context = {
            'topic': topic,
            'videos': videos,
            'resources': resources,
            'questions': questions,
            'video_form': video_form,
            'resource_form': resource_form,
            'show_modal': f'material_edit_{material.id}',
        }
        return render(request, 'portal/topic_edit.html', context)
    else:
        return redirect('portal_topic_edit', topic_id=material.topic_id)

@admin_required
def portal_questions_manage(request, topic_id):
    topic = get_object_or_404(Topic, id=topic_id)
    questions = topic.questions.prefetch_related('choices').all().order_by('order', 'id')
    site_config = SiteConfig.get_solo()
    
    context = {
        'topic': topic,
        'questions': questions,
        'required_question_count': site_config.default_required_question_count,
        'current_count': questions.count(),
    }
    return render(request, 'portal/question_edit.html', context)

@admin_required
def portal_question_add(request, topic_id):
    topic = get_object_or_404(Topic, id=topic_id)
    if request.method == 'POST':
        question_text = request.POST.get('question_text', '').strip()
        question_type = request.POST.get('question_type', Question.TYPE_SINGLE_CHOICE)
        is_required = request.POST.get('required') in ('true', 'True', 'on', '1') or 'required' in request.POST
        accepted_answers = request.POST.get('accepted_answers', '').strip()
        
        if not question_text:
            messages.error(request, "Question text cannot be empty.")
            site_config = SiteConfig.get_solo()
            submitted_data = request.POST.copy()
            submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
            context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
            return render(request, 'portal/question_edit.html', context)

        if question_type == Question.TYPE_SINGLE_CHOICE:
            choice_texts = [
                request.POST.get(f'choice_{i}', '').strip() 
                for i in range(1, 7) 
                if request.POST.get(f'choice_{i}', '').strip()
            ]
            correct_index = request.POST.get('correct_choice')
            if len(choice_texts) < 2:
                messages.error(request, "Single Choice questions must have at least 2 answer choices.")
                site_config = SiteConfig.get_solo()
                submitted_data = request.POST.copy()
                submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
                context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
                return render(request, 'portal/question_edit.html', context)
            if not correct_index:
                messages.error(request, "Please mark exactly one choice as the correct answer.")
                site_config = SiteConfig.get_solo()
                submitted_data = request.POST.copy()
                submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
                context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
                return render(request, 'portal/question_edit.html', context)
            try:
                correct_idx = int(correct_index)
            except ValueError:
                messages.error(request, "Invalid correct choice selection.")
                site_config = SiteConfig.get_solo()
                submitted_data = request.POST.copy()
                submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
                context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
                return render(request, 'portal/question_edit.html', context)

            with transaction.atomic():
                question = Question.objects.create(
                    topic=topic,
                    text=question_text,
                    question_type=Question.TYPE_SINGLE_CHOICE,
                    required=is_required
                )
                for idx, c_text in enumerate(choice_texts, start=1):
                    Choice.objects.create(
                        question=question,
                        text=c_text,
                        is_correct=(idx == correct_idx)
                    )

        elif question_type == Question.TYPE_MULTIPLE_CHOICE:
            choice_texts = [
                request.POST.get(f'choice_{i}', '').strip() 
                for i in range(1, 7) 
                if request.POST.get(f'choice_{i}', '').strip()
            ]
            correct_indices = [
                int(i) for i in request.POST.getlist('correct_choices') if i.isdigit()
            ]
            if len(choice_texts) < 2:
                messages.error(request, "Multiple Choice questions must have at least 2 answer choices.")
                site_config = SiteConfig.get_solo()
                submitted_data = request.POST.copy()
                submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
                context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
                return render(request, 'portal/question_edit.html', context)
            if not correct_indices:
                messages.error(request, "Please mark at least one choice as a correct answer.")
                site_config = SiteConfig.get_solo()
                submitted_data = request.POST.copy()
                submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
                context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
                return render(request, 'portal/question_edit.html', context)

            with transaction.atomic():
                question = Question.objects.create(
                    topic=topic,
                    text=question_text,
                    question_type=Question.TYPE_MULTIPLE_CHOICE,
                    required=is_required
                )
                for idx, c_text in enumerate(choice_texts, start=1):
                    Choice.objects.create(
                        question=question,
                        text=c_text,
                        is_correct=(idx in correct_indices)
                    )

        elif question_type == Question.TYPE_TRUE_FALSE:
            tf_correct = request.POST.get('tf_correct', 'true').lower()
            with transaction.atomic():
                question = Question.objects.create(
                    topic=topic,
                    text=question_text,
                    question_type=Question.TYPE_TRUE_FALSE,
                    required=is_required
                )
                Choice.objects.create(question=question, text="True", is_correct=(tf_correct == 'true'))
                Choice.objects.create(question=question, text="False", is_correct=(tf_correct == 'false'))

        elif question_type == Question.TYPE_SHORT_ANSWER:
            if not accepted_answers:
                messages.error(request, "Short Answer questions require at least one accepted answer.")
                site_config = SiteConfig.get_solo()
                submitted_data = request.POST.copy()
                submitted_data['correct_choices_list'] = request.POST.getlist('correct_choices')
                context = {'topic': topic, 'questions': topic.questions.prefetch_related('choices').all().order_by('order', 'id'), 'required_question_count': site_config.default_required_question_count, 'current_count': topic.questions.count(), 'show_modal': 'addQuestionModal', 'submitted_data': submitted_data}
                return render(request, 'portal/question_edit.html', context)
            Question.objects.create(
                topic=topic,
                text=question_text,
                question_type=Question.TYPE_SHORT_ANSWER,
                required=is_required,
                accepted_answers=accepted_answers
            )

        elif question_type == Question.TYPE_PARAGRAPH:
            Question.objects.create(
                topic=topic,
                text=question_text,
                question_type=Question.TYPE_PARAGRAPH,
                required=is_required
            )

        messages.success(request, "Question saved successfully.")
    return redirect('portal_questions_manage', topic_id=topic.id)

@admin_required
def portal_question_delete(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    topic_id = question.topic_id
    if request.method == 'POST':
        try:
            question.delete()
            messages.success(request, "Question deleted successfully.")
        except ProtectedError:
            messages.error(request, "Cannot delete this question because it is referenced by existing quiz attempts.")
    return redirect('portal_questions_manage', topic_id=topic_id)

@admin_required
def portal_questions_reorder(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            topic_id = data.get('topic_id')
            question_ids = data.get('question_ids', [])
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON format.'}, status=400)

        if not topic_id:
            return JsonResponse({'success': False, 'error': 'Topic ID is required.'}, status=400)
        
        if not question_ids:
            return JsonResponse({'success': False, 'error': 'No question IDs provided.'}, status=400)
            
        try:
            topic_id = int(topic_id)
            question_ids = [int(qid) for qid in question_ids]
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid ID format.'}, status=400)
            
        if not Topic.objects.filter(id=topic_id).exists():
            return JsonResponse({'success': False, 'error': 'Topic does not exist.'}, status=400)

        with transaction.atomic():
            current_questions = list(Question.objects.filter(topic_id=topic_id).values_list('id', flat=True))
            
            if len(question_ids) != len(current_questions):
                return JsonResponse({'success': False, 'error': 'Submitted list length does not match existing questions.'}, status=400)
                
            if len(question_ids) != len(set(question_ids)):
                return JsonResponse({'success': False, 'error': 'Duplicate IDs found in submission.'}, status=400)
                
            if set(question_ids) != set(current_questions):
                return JsonResponse({'success': False, 'error': 'Submitted IDs do not exactly match existing questions for this topic.'}, status=400)
                
            for index, qid in enumerate(question_ids, start=1):
                Question.objects.filter(id=qid).update(order=index)
                
        return JsonResponse({'success': True})
    return HttpResponseBadRequest("POST required")

# --- GLOBAL OVERVIEWS (VIDEOS, MATERIALS, QUIZZES) ---

@admin_required
def portal_videos_overview(request):
    subject_id = request.GET.get('subject_id')
    topic_id = request.GET.get('topic_id')
    
    selected_subject = None
    selected_topic = None
    videos = Video.objects.select_related('topic__subject').all()
    
    if subject_id:
        from content.models import Subject
        try:
            selected_subject = Subject.objects.get(id=subject_id)
        except (ValueError, Subject.DoesNotExist):
            selected_subject = None
            subject_id = None
            
    if topic_id:
        try:
            selected_topic = Topic.objects.get(id=topic_id)
            if selected_subject and selected_topic.subject_id != selected_subject.id:
                selected_topic = None
            else:
                if not selected_subject:
                    selected_subject = selected_topic.subject
        except (ValueError, Topic.DoesNotExist):
            selected_topic = None

    if selected_topic:
        videos = videos.filter(topic=selected_topic).order_by('order', 'id')
    elif selected_subject:
        videos = videos.filter(topic__subject=selected_subject).order_by('topic__order', 'order', 'id')
    else:
        videos = videos.order_by('topic__subject__order', 'topic__order', 'order', 'id')
        
    from content.models import Subject
    subjects = Subject.objects.all().order_by('order', 'id')
    
    topics = Topic.objects.select_related('subject').all()
    if selected_subject:
        topics = topics.filter(subject=selected_subject)
    topics = topics.order_by('subject__order', 'order')
    
    return render(request, 'portal/videos_list.html', {
        'videos': videos,
        'subjects': subjects,
        'topics': topics,
        'selected_subject': selected_subject,
        'selected_topic': selected_topic
    })

@admin_required
def portal_videos_reorder(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            topic_id = data.get('topic_id')
            video_ids = data.get('video_ids', [])
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON format.'}, status=400)

        if not topic_id:
            return JsonResponse({'success': False, 'error': 'Topic ID is required.'}, status=400)
        
        if not video_ids:
            return JsonResponse({'success': False, 'error': 'No video IDs provided.'}, status=400)
            
        try:
            topic_id = int(topic_id)
            video_ids = [int(vid) for vid in video_ids]
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid ID format.'}, status=400)
            
        if not Topic.objects.filter(id=topic_id).exists():
            return JsonResponse({'success': False, 'error': 'Topic does not exist.'}, status=400)

        with transaction.atomic():
            current_videos = list(Video.objects.filter(topic_id=topic_id).values_list('id', flat=True))
            
            if len(video_ids) != len(current_videos):
                return JsonResponse({'success': False, 'error': 'Submitted list length does not match existing videos.'}, status=400)
                
            if len(video_ids) != len(set(video_ids)):
                return JsonResponse({'success': False, 'error': 'Duplicate IDs found in submission.'}, status=400)
                
            if set(video_ids) != set(current_videos):
                return JsonResponse({'success': False, 'error': 'Submitted IDs do not exactly match existing videos for this topic.'}, status=400)
                
            for index, vid in enumerate(video_ids, start=1):
                Video.objects.filter(id=vid).update(order=index)
                
        return JsonResponse({'success': True})
    return HttpResponseBadRequest("POST required")
@admin_required
def portal_resources_reorder(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON format.'}, status=400)

        if not isinstance(data, dict):
            return JsonResponse({'success': False, 'error': 'JSON payload must be an object.'}, status=400)

        topic_id = data.get('topic_id')
        resource_ids = data.get('resource_ids')

        if not topic_id:
            return JsonResponse({'success': False, 'error': 'Topic ID is required.'}, status=400)
        
        if not isinstance(resource_ids, list):
            return JsonResponse({'success': False, 'error': 'resource_ids must be a list.'}, status=400)
        
        if not resource_ids:
            return JsonResponse({'success': False, 'error': 'No resource IDs provided.'}, status=400)
            
        try:
            topic_id = int(topic_id)
            resource_ids = [int(rid) for rid in resource_ids]
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid ID format.'}, status=400)
            
        if not Topic.objects.filter(id=topic_id).exists():
            return JsonResponse({'success': False, 'error': 'Topic does not exist.'}, status=400)

        with transaction.atomic():
            current_resources = list(Resource.objects.filter(topic_id=topic_id).values_list('id', flat=True))
            
            if len(resource_ids) != len(current_resources):
                return JsonResponse({'success': False, 'error': 'Submitted list length does not match existing resources.'}, status=400)
                
            if len(resource_ids) != len(set(resource_ids)):
                return JsonResponse({'success': False, 'error': 'Duplicate IDs found in submission.'}, status=400)
                
            if set(resource_ids) != set(current_resources):
                return JsonResponse({'success': False, 'error': 'Submitted IDs do not exactly match existing resources for this topic.'}, status=400)
                
            for index, rid in enumerate(resource_ids, start=1):
                Resource.objects.filter(id=rid).update(order=index)
                
        return JsonResponse({'success': True})
    return HttpResponseBadRequest("POST required")

@admin_required
def portal_materials_overview(request):
    subject_id = request.GET.get('subject_id')
    topic_id = request.GET.get('topic_id')
    
    selected_subject = None
    selected_topic = None
    materials = Resource.objects.select_related('topic__subject').all()
    
    if subject_id:
        from content.models import Subject
        try:
            selected_subject = Subject.objects.get(id=subject_id)
        except (ValueError, Subject.DoesNotExist):
            selected_subject = None
            subject_id = None
            
    if topic_id:
        try:
            selected_topic = Topic.objects.get(id=topic_id)
            if selected_subject and selected_topic.subject_id != selected_subject.id:
                selected_topic = None
            else:
                if not selected_subject:
                    selected_subject = selected_topic.subject
        except (ValueError, Topic.DoesNotExist):
            selected_topic = None

    if selected_topic:
        materials = materials.filter(topic=selected_topic).order_by('order', 'id')
    elif selected_subject:
        materials = materials.filter(topic__subject=selected_subject).order_by('topic__order', 'order', 'id')
    else:
        materials = materials.order_by('topic__subject__order', 'topic__order', 'order', 'id')
        
    from content.models import Subject
    subjects = Subject.objects.all().order_by('order', 'id')
    
    topics = Topic.objects.select_related('subject').all()
    if selected_subject:
        topics = topics.filter(subject=selected_subject)
    topics = topics.order_by('subject__order', 'order')
    
    return render(request, 'portal/materials_list.html', {
        'materials': materials,
        'subjects': subjects,
        'topics': topics,
        'selected_subject': selected_subject,
        'selected_topic': selected_topic
    })

@admin_required
def portal_quizzes_overview(request):
    subject_id = request.GET.get('subject_id')
    topic_id = request.GET.get('topic_id')
    
    selected_subject = None
    selected_topic = None
    topics_queryset = Topic.objects.select_related('subject').prefetch_related('questions').all()
    
    if subject_id:
        from content.models import Subject
        try:
            selected_subject = Subject.objects.get(id=subject_id)
        except (ValueError, Subject.DoesNotExist):
            selected_subject = None
            subject_id = None
            
    if topic_id:
        try:
            selected_topic = Topic.objects.get(id=topic_id)
            if selected_subject and selected_topic.subject_id != selected_subject.id:
                selected_topic = None
            else:
                if not selected_subject:
                    selected_subject = selected_topic.subject
        except (ValueError, Topic.DoesNotExist):
            selected_topic = None

    if selected_topic:
        topics_queryset = topics_queryset.filter(id=selected_topic.id).order_by('order', 'id')
    elif selected_subject:
        topics_queryset = topics_queryset.filter(subject=selected_subject).order_by('order', 'id')
    else:
        topics_queryset = topics_queryset.order_by('subject__order', 'order', 'id')
        
    from content.models import Subject
    subjects = Subject.objects.all().order_by('order', 'id')
    
    topics_for_dropdown = Topic.objects.select_related('subject').all()
    if selected_subject:
        topics_for_dropdown = topics_for_dropdown.filter(subject=selected_subject)
    topics_for_dropdown = topics_for_dropdown.order_by('subject__order', 'order')

    site_config = SiteConfig.get_solo()
    
    quiz_data = []
    for t in topics_queryset:
        q_count = t.questions.count()
        quiz_data.append({
            'topic': t,
            'question_count': q_count,
            'is_ready': q_count >= site_config.default_required_question_count,
        })
        
    return render(request, 'portal/quizzes_list.html', {
        'quiz_data': quiz_data, 
        'required_count': site_config.default_required_question_count,
        'subjects': subjects,
        'topics': topics_for_dropdown,
        'selected_subject': selected_subject,
        'selected_topic': selected_topic
    })

# --- ADMIN ATTEMPT HISTORY ---

@admin_required
def admin_attempts_list(request):
    attempts = (
        QuizAttempt.objects.select_related('user', 'topic')
        .prefetch_related('responses')
        .order_by('-created_at')
    )
    attempts_data = []
    for a in attempts:
        requires_review = a.responses.filter(is_correct__isnull=True).exists()
        gradable = a.responses.filter(is_correct__isnull=False).count()
        display_score = a.score if gradable > 0 else None  # None -> N/A in template
        attempts_data.append({
            'attempt': a,
            'requires_review': requires_review,
            'display_score': display_score,
        })
    return render(request, 'portal/admin_attempts_list.html', {'attempts_data': attempts_data})

@admin_required
def admin_attempt_detail(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    responses = (
        attempt.responses.select_related('question')
        .prefetch_related('selected_choices')
        .order_by('id')
    )
    resp_data = []
    for r in responses:
        q = r.question
        # learner answer
        if q.question_type in [Question.TYPE_SINGLE_CHOICE, Question.TYPE_MULTIPLE_CHOICE, Question.TYPE_TRUE_FALSE]:
            learner_answer = ", ".join([c.text for c in r.selected_choices.all()])
        else:
            learner_answer = r.text_response or ""
        # correct answer for gradable types
        correct_answer = None
        if q.is_gradable:
            if q.question_type == Question.TYPE_SINGLE_CHOICE:
                correct = q.choices.filter(is_correct=True).first()
                correct_answer = correct.text if correct else ''
            elif q.question_type == Question.TYPE_MULTIPLE_CHOICE:
                correct = q.choices.filter(is_correct=True)
                correct_answer = ", ".join([c.text for c in correct])
            elif q.question_type == Question.TYPE_TRUE_FALSE:
                correct = q.choices.filter(is_correct=True).first()
                correct_answer = correct.text if correct else ''
            elif q.question_type == Question.TYPE_SHORT_ANSWER:
                correct_answer = q.accepted_answers
        resp_data.append({
            'response': r,
            'question_text': q.text,
            'question_type': q.get_question_type_display(),
            'learner_answer': learner_answer,
            'correct_answer': correct_answer,
            'is_pending': r.is_correct is None,
        })
    # compute summary for display
    gradable = attempt.responses.filter(is_correct__isnull=False).count()
    display_score = attempt.score if gradable > 0 else None
    context = {
        'attempt': attempt,
        'responses_data': resp_data,
        'display_score': display_score,
    }
    return render(request, 'portal/admin_attempt_detail.html', context)

@admin_required
def admin_learner_topic_attempts(request, learner_id, topic_id):
    learner = get_object_or_404(User, id=learner_id, role=User.ROLE_LEARNER)
    topic = get_object_or_404(Topic, id=topic_id)
    attempts = (
        QuizAttempt.objects.filter(user=learner, topic=topic)
        .select_related('user', 'topic')
        .prefetch_related('responses')
        .order_by('-created_at')
    )
    attempts_data = []
    for a in attempts:
        requires_review = a.responses.filter(is_correct__isnull=True).exists()
        gradable = a.responses.filter(is_correct__isnull=False).count()
        display_score = a.score if gradable > 0 else None
        attempts_data.append({
            'attempt': a,
            'requires_review': requires_review,
            'display_score': display_score,
            'is_gradable': gradable > 0,
        })
    context = {
        'learner': learner,
        'topic': topic,
        'attempts_data': attempts_data,
    }
    return render(request, 'portal/admin_topic_attempts.html', context)

@admin_required
def admin_attempt_review(request, attempt_id, response_id):
    if request.method != 'POST':
        return redirect('admin_attempt_detail', attempt_id=attempt_id)
    action = request.POST.get('action')
    response = get_object_or_404(QuizResponse, id=response_id, attempt_id=attempt_id)
    if response.is_correct is not None:
        messages.error(request, "This response has already been graded and cannot be modified.")
        return redirect('admin_attempt_detail', attempt_id=attempt_id)
    if action == 'approve':
        response.is_correct = True
        messages.success(request, "Response approved as correct.")
    elif action == 'reject':
        response.is_correct = False
        messages.success(request, "Response marked as incorrect.")
    else:
        messages.error(request, "Invalid review action.")
        return redirect('admin_attempt_detail', attempt_id=attempt_id)
    response.save()
    # Recalculate attempt totals (score, etc.)
    attempt = response.attempt
    recalculate_attempt_score(attempt)

    attempt.refresh_from_db()

    return redirect('admin_attempt_detail', attempt_id=attempt_id)

# --- GLOBAL OVERVIEWS (VIDEOS, MATERIALS, QUIZZES) ---

# --- LEARNERS MANAGEMENT ---

@admin_required
def portal_learners_list(request):
    learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
    learners_data = [{'learner': l} for l in learners]
        
    form = LearnerForm()
    empty_reset_form = LearnerPasswordResetForm()
    context = {
        'learners_data': learners_data,
        'form': form,
        'empty_reset_form': empty_reset_form,
    }
    return render(request, 'portal/learners_list.html', context)

@admin_required
def portal_progress_list(request):
    learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
    learners_data = []
    
    for l in learners:
        prog = get_overall_learner_progress(l)
        learners_data.append({
            'learner': l,
            'overall_progress': prog,
        })
        
    context = {
        'learners_data': learners_data,
    }
    return render(request, 'portal/progress_list.html', context)

@admin_required
def portal_learner_add(request):
    if request.method == 'POST':
        form = LearnerForm(request.POST)
        if form.is_valid():
            learner = form.save(commit=False)
            learner.role = User.ROLE_LEARNER
            raw_password = form.cleaned_data.get('password') or 'password123'
            learner.set_password(raw_password)
            learner.save()
            messages.success(request, f"Learner account @{learner.username} created successfully with password: '{raw_password}'.")
            return redirect('portal_learners_list')
        else:
            messages.error(request, "Error creating learner account. Username may already exist.")
    return redirect('portal_learners_list')

@admin_required
def portal_learner_edit(request, learner_id):
    learner = get_object_or_404(User, id=learner_id, role=User.ROLE_LEARNER)
    if request.method == 'POST':
        form = LearnerEditForm(request.POST, instance=learner)
        if form.is_valid():
            form.save()
            messages.success(request, f"Learner details for @{learner.username} updated successfully.")
            return redirect('portal_learners_list')
        
        # Validation failure
        learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
        learners_data = [{'learner': l} for l in learners]
        context = {
            'learners_data': learners_data,
            'form': LearnerForm(),  # Add form
            'empty_reset_form': LearnerPasswordResetForm(),
            'edit_form': form,
            'edit_learner': learner,
            'show_modal': f'learner_edit'
        }
        return render(request, 'portal/learners_list.html', context)
    else:
        learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
        learners_data = [{'learner': l} for l in learners]
        form = LearnerEditForm(instance=learner)
        context = {
            'learners_data': learners_data,
            'form': LearnerForm(),
            'empty_reset_form': LearnerPasswordResetForm(),
            'edit_form': form,
            'edit_learner': learner,
            'show_modal': f'learner_edit'
        }
        return render(request, 'portal/learners_list.html', context)

@admin_required
def portal_learner_toggle_status(request, learner_id):
    learner = get_object_or_404(User, id=learner_id, role=User.ROLE_LEARNER)
    if request.method == 'POST':
        learner.is_active = not learner.is_active
        learner.save()
        status_str = "activated" if learner.is_active else "deactivated"
        messages.success(request, f"Learner account @{learner.username} has been {status_str}.")
    return redirect('portal_learners_list')

@admin_required
def portal_learner_reset_password(request, learner_id):
    learner = get_object_or_404(User, id=learner_id, role=User.ROLE_LEARNER)
    if request.method == 'POST':
        form = LearnerPasswordResetForm(request.POST, user=learner)
        if form.is_valid():
            new_password = form.cleaned_data['new_password']
            learner.set_password(new_password)
            learner.save()
            messages.success(request, f"Password reset successfully for @{learner.username}.")
            return redirect('portal_learners_list')
        
        # Validation failure
        learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
        learners_data = [{'learner': l} for l in learners]
        context = {
            'learners_data': learners_data,
            'form': LearnerForm(),
            'empty_reset_form': LearnerPasswordResetForm(),
            'reset_form': form,
            'reset_learner': learner,
            'show_modal': 'resetPasswordModal'
        }
        return render(request, 'portal/learners_list.html', context)
    else:
        # GET request
        learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
        learners_data = [{'learner': l} for l in learners]
        form = LearnerPasswordResetForm(user=learner)
        context = {
            'learners_data': learners_data,
            'form': LearnerForm(),
            'empty_reset_form': LearnerPasswordResetForm(),
            'reset_form': form,
            'reset_learner': learner,
            'show_modal': 'resetPasswordModal'
        }
        return render(request, 'portal/learners_list.html', context)

# --- LEARNER PROGRESS DRILL-DOWN ---

@admin_required
def portal_learner_progress_detail(request, learner_id):
    learner = get_object_or_404(User, id=learner_id)
    active_subjects = Subject.objects.filter(is_active=True)
    
    subjects_breakdown = []
    for subject in active_subjects:
        topics = subject.topics.filter(status=Topic.STATUS_PUBLISHED)
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
    return render(request, 'portal/learner_detail.html', context)

# --- LMS SETTINGS ---

@admin_required
def portal_learner_access(request, learner_id):
    """Render per‑learner access UI."""
    from .forms import LearnerAccessForm
    learner = get_object_or_404(User, id=learner_id, role=User.ROLE_LEARNER)
    subjects = Subject.objects.filter(is_active=True).prefetch_related('topics', 'topics__videos', 'topics__resources')
    hierarchy = []
    for subject in subjects:
        # Build hierarchy for the target learner
        from django.contrib.contenttypes.models import ContentType
        def explicit_denial(model, obj_id):
            ct = ContentType.objects.get_for_model(model)
            return LearnerAccess.objects.filter(learner=learner, content_type=ct, object_id=obj_id, is_allowed=False).exists()

        subject_allowed = access_services.has_subject_access(learner, subject)
        subject_entry = {
            'type': 'subject',
            'id': subject.id,
            'name': subject.name,
            'allowed': subject_allowed,
            'explicit_denied': not subject_allowed and explicit_denial(Subject, subject.id),
            'children': [],
        }
        for topic in subject.topics.filter(status=Topic.STATUS_PUBLISHED):
            topic_allowed = subject_allowed and access_services.has_topic_access(learner, topic)
            topic_entry = {
                'type': 'topic',
                'id': topic.id,
                'name': topic.name,
                'allowed': topic_allowed,
                'explicit_denied': not topic_allowed and explicit_denial(Topic, topic.id),
                'inherited_denied': not subject_allowed,
                'children': [],
            }
            for video in topic.videos.all():
                video_allowed = topic_allowed and access_services.has_video_access(learner, video)
                video_entry = {
                    'type': 'video',
                    'id': video.id,
                    'name': video.title,
                    'allowed': video_allowed,
                    'explicit_denied': not video_allowed and explicit_denial(Video, video.id),
                    'inherited_denied': not topic_allowed,
                }
                topic_entry['children'].append(video_entry)
            for resource in topic.resources.all():
                resource_allowed = topic_allowed and access_services.has_resource_access(learner, resource)
                resource_entry = {
                    'type': 'resource',
                    'id': resource.id,
                    'name': resource.title,
                    'allowed': resource_allowed,
                    'explicit_denied': not resource_allowed and explicit_denial(Resource, resource.id),
                    'inherited_denied': not topic_allowed,
                }
                topic_entry['children'].append(resource_entry)
            subject_entry['children'].append(topic_entry)
        hierarchy.append(subject_entry)
    form = LearnerAccessForm()
    context = {'learner': learner, 'hierarchy': hierarchy, 'form': form}
    return render(request, 'portal/learner_access.html', context)

@admin_required
def portal_learner_access_save(request, learner_id):
    """Process POST from access UI – only explicit denies are persisted."""
    if request.method != 'POST':
        return HttpResponseBadRequest('Invalid method')
    learner = get_object_or_404(User, id=learner_id, role=User.ROLE_LEARNER)
    form = LearnerAccessForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid access data submitted.')
        return redirect('portal_learner_access', learner_id=learner_id)
    cleaned = form.cleaned_data['access_data']
    with transaction.atomic():
        from content.models import Subject, Topic, Video, Resource
        from django.contrib.contenttypes.models import ContentType
        model_map = {'subject': Subject, 'topic': Topic, 'video': Video, 'resource': Resource}
        denied_set = set((e['type'], e['id']) for e in cleaned)
        existing = LearnerAccess.objects.filter(learner=learner, is_allowed=False)
        for la in list(existing):
            ct_model = la.content_type.model
            if (ct_model, la.object_id) not in denied_set:
                la.delete()
        def parent_denied(entry_type, obj_id):
            if entry_type == 'topic':
                try:
                    t = Topic.objects.get(pk=obj_id)
                    return ('subject', t.subject_id) in denied_set
                except Topic.DoesNotExist:
                    return False
            if entry_type in ('video', 'resource'):
                try:
                    obj = Video.objects.get(pk=obj_id) if entry_type == 'video' else Resource.objects.get(pk=obj_id)
                    return ('topic', obj.topic_id) in denied_set
                except (Video.DoesNotExist, Resource.DoesNotExist):
                    return False
            return False
        for entry in cleaned:
            typ, obj_id = entry['type'], entry['id']
            if parent_denied(typ, obj_id):
                continue
            Model = model_map[typ]
            obj = Model.objects.get(pk=obj_id)
            ct = ContentType.objects.get_for_model(obj)
            la, created = LearnerAccess.objects.get_or_create(
                learner=learner, content_type=ct, object_id=obj_id,
                defaults={'is_allowed': False}
            )
            if not created and la.is_allowed is not False:
                la.is_allowed = False
                la.save()
    messages.success(request, 'Access settings saved.')
    return redirect('portal_learner_access', learner_id=learner_id)

@admin_required
def portal_settings(request):
    site_config = SiteConfig.get_solo()
    if request.method == 'POST':
        form = SiteConfigForm(request.POST, instance=site_config)
        if form.is_valid():
            form.save()
            messages.success(request, "LMS Settings updated successfully.")
            return redirect('portal_settings')
    else:
        form = SiteConfigForm(instance=site_config)

    return render(request, 'portal/settings.html', {'form': form, 'site_config': site_config})
