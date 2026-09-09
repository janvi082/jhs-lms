from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.db import transaction
import secrets
import string

from content.models import Subject, Topic, Video, Resource, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt
from accounts.models import User
from progress.models import TopicProgress
from progress.services import (
    get_subject_progress_summary,
    get_overall_learner_progress,
    get_topic_display_status
)
from .decorators import admin_required
from .forms import (
    SubjectForm,
    TopicForm,
    TopicAddForm,
    VideoForm,
    ResourceForm,
    QuestionForm,
    LearnerForm,
    SiteConfigForm
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
        topic_ids = request.POST.getlist('topic_ids[]')
        for index, topic_id in enumerate(topic_ids, start=1):
            Topic.objects.filter(id=topic_id).update(order=index)
        return JsonResponse({'status': 'ok'})
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
            # PUBLISH GATE CHECK
            if target_status == Topic.STATUS_PUBLISHED and not topic.is_assessment_ready():
                if required_count > 0:
                    needed = required_count - current_question_count
                    msg = f"This topic has only {current_question_count} questions. Please add {needed} more before publishing."
                else:
                    msg = "This topic has no questions. Please add at least one question before publishing."
                messages.error(request, msg)
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
            if not video.order:
                video.order = topic.videos.count() + 1
            video.save()
            messages.success(request, f"Video tutorial '{video.title}' added successfully.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Failed to add video tutorial ({field}): {error}")
    return redirect('portal_topic_edit', topic_id=topic.id)

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
            if not material.order:
                material.order = topic.resources.count() + 1
            material.save()
            messages.success(request, f"Learning material '{material.title}' added successfully.")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Failed to add learning material ({field}): {error}")
    return redirect('portal_topic_edit', topic_id=topic.id)

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
def portal_choice_delete(request, choice_id):
    choice = get_object_or_404(Choice, id=choice_id)
    # Schema limitation: no historical per‑choice reference in QuizAttempt
    messages.warning(request, "The current V1 schema does not retain historical choice‑level references, so choice‑history deletion protection cannot be reliably implemented without a schema change.")
    # Proceed with deletion
    choice.delete()
    messages.success(request, "Choice deleted successfully.")
    return redirect('portal_topic_edit', topic_id=choice.question.topic_id)

# --- QUESTION & CHOICE EDITOR ---

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
        order = int(request.POST.get('order', topic.questions.count() + 1))
        
        choice_texts = [
            request.POST.get(f'choice_{i}', '').strip() 
            for i in range(1, 7) 
            if request.POST.get(f'choice_{i}', '').strip()
        ]
        
        correct_index = request.POST.get('correct_choice')
        
        # VALIDATION RULES: >= 2 choices, exactly 1 marked correct
        if not question_text:
            messages.error(request, "Question text cannot be empty.")
            return redirect('portal_questions_manage', topic_id=topic.id)
            
        if len(choice_texts) < 2:
            messages.error(request, "Each question must have at least 2 answer choices.")
            return redirect('portal_questions_manage', topic_id=topic.id)
            
        if correct_index is None:
            messages.error(request, "Please mark exactly one choice as the correct answer.")
            return redirect('portal_questions_manage', topic_id=topic.id)
            
        try:
            correct_idx = int(correct_index)
        except ValueError:
            messages.error(request, "Invalid correct choice selection.")
            return redirect('portal_questions_manage', topic_id=topic.id)

        with transaction.atomic():
            question = Question.objects.create(topic=topic, text=question_text, order=order)
            for idx, c_text in enumerate(choice_texts, start=1):
                Choice.objects.create(
                    question=question,
                    text=c_text,
                    is_correct=(idx == correct_idx)
                )
        messages.success(request, "Question and answer options saved successfully.")
    return redirect('portal_questions_manage', topic_id=topic.id)

@admin_required
def portal_question_delete(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    topic_id = question.topic_id
    if request.method == 'POST':
        question.delete()
        messages.success(request, "Question deleted successfully.")
    return redirect('portal_questions_manage', topic_id=topic_id)

# --- GLOBAL OVERVIEWS (VIDEOS, MATERIALS, QUIZZES) ---

@admin_required
def portal_videos_overview(request):
    videos = Video.objects.select_related('topic__subject').all().order_by('topic__subject__order', 'topic__order')
    return render(request, 'portal/videos_list.html', {'videos': videos})

@admin_required
def portal_materials_overview(request):
    materials = Resource.objects.select_related('topic__subject').all().order_by('topic__subject__order', 'topic__order')
    return render(request, 'portal/materials_list.html', {'materials': materials})

@admin_required
def portal_quizzes_overview(request):
    topics = Topic.objects.select_related('subject').prefetch_related('questions').all()
    site_config = SiteConfig.get_solo()
    
    quiz_data = []
    for t in topics:
        q_count = t.questions.count()
        quiz_data.append({
            'topic': t,
            'question_count': q_count,
            'is_ready': q_count >= site_config.default_required_question_count,
        })
        
    return render(request, 'portal/quizzes_list.html', {'quiz_data': quiz_data, 'required_count': site_config.default_required_question_count})

# --- LEARNERS MANAGEMENT ---

@admin_required
def portal_learners_list(request):
    learners = User.objects.filter(role=User.ROLE_LEARNER).order_by('-date_joined')
    learners_data = []
    
    for l in learners:
        prog = get_overall_learner_progress(l)
        learners_data.append({
            'learner': l,
            'overall_progress': prog,
        })
        
    form = LearnerForm()
    context = {
        'learners_data': learners_data,
        'form': form,
    }
    return render(request, 'portal/learners_list.html', context)

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
        new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        learner.set_password(new_password)
        learner.save()
        messages.success(request, f"Password for @{learner.username} reset successfully to: '{new_password}'")
    return redirect('portal_learners_list')

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
