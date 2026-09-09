from django.urls import path
from . import views

urlpatterns = [
    # Dashboard redirect / Subjects List
    path('', views.portal_subjects_list, name='portal_dashboard'),
    
    # Subjects
    path('subjects/', views.portal_subjects_list, name='portal_subjects_list'),
    path('subjects/add/', views.portal_subject_add, name='portal_subject_add'),
    path('subjects/<int:subject_id>/edit/', views.portal_subject_edit, name='portal_subject_edit'),
    path('subjects/<int:subject_id>/archive/', views.portal_subject_archive, name='portal_subject_archive'),
    
    # Topics
    path('topics/', views.portal_topics_list, name='portal_topics_list'),
    path('subjects/<int:subject_id>/topics/', views.portal_topics_list, name='portal_subject_topics_list'),
    path('topics/add/', views.portal_topic_add, name='portal_topic_add'),
    path('topics/reorder/', views.portal_topics_reorder, name='portal_topics_reorder'),
    path('topics/<int:topic_id>/edit/', views.portal_topic_edit, name='portal_topic_edit'),
    
    # Inline Videos & Materials
    path('videos/<int:video_id>/edit/', views.portal_video_edit, name='portal_video_edit'),
    path('materials/<int:material_id>/edit/', views.portal_material_edit, name='portal_material_edit'),
    path('choices/<int:choice_id>/delete/', views.portal_choice_delete, name='portal_choice_delete'),
    path('topics/<int:topic_id>/videos/add/', views.portal_video_add, name='portal_video_add'),
    path('videos/<int:video_id>/delete/', views.portal_video_delete, name='portal_video_delete'),
    path('topics/<int:topic_id>/materials/add/', views.portal_material_add, name='portal_material_add'),
    path('materials/<int:material_id>/delete/', views.portal_material_delete, name='portal_material_delete'),
    
    # Questions & Choices
    path('topics/<int:topic_id>/questions/', views.portal_questions_manage, name='portal_questions_manage'),
    path('topics/<int:topic_id>/questions/add/', views.portal_question_add, name='portal_question_add'),
    path('questions/<int:question_id>/delete/', views.portal_question_delete, name='portal_question_delete'),
    
    # Global Overviews
    path('videos/', views.portal_videos_overview, name='portal_videos_overview'),
    path('materials/', views.portal_materials_overview, name='portal_materials_overview'),
    path('quizzes/', views.portal_quizzes_overview, name='portal_quizzes_overview'),
    
    # Learners Management & Progress
    path('learners/', views.portal_learners_list, name='portal_learners_list'),
    path('learners/add/', views.portal_learner_add, name='portal_learner_add'),
    path('learners/<int:learner_id>/toggle-status/', views.portal_learner_toggle_status, name='portal_learner_toggle_status'),
    path('learners/<int:learner_id>/reset-password/', views.portal_learner_reset_password, name='portal_learner_reset_password'),
    path('progress/', views.portal_learners_list, name='portal_progress'),
    path('learners/<int:learner_id>/', views.portal_learner_progress_detail, name='portal_learner_progress_detail'),
    
    # LMS Settings
    path('settings/', views.portal_settings, name='portal_settings'),
]
