from django.contrib import admin
from adminsortable2.admin import SortableAdminMixin, SortableInlineAdminMixin
from .models import SiteConfig, Subject, Topic, Video, Resource
from quizzes.models import Question, Choice

@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ('default_passing_score', 'default_required_question_count')
    
    def has_add_permission(self, request):
        # Singleton: prevent adding more than 1 instance
        if SiteConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

class TopicInline(SortableInlineAdminMixin, admin.TabularInline):
    model = Topic
    extra = 1
    fields = ('name', 'summary', 'order', 'is_active', 'passing_score_override')
    show_change_link = True

@admin.register(Subject)
class SubjectAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'order', 'topic_count_display', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [TopicInline]

    def topic_count_display(self, obj):
        return f"{obj.topics.count()} Topics"
    topic_count_display.short_description = "Topics"

class VideoInline(SortableInlineAdminMixin, admin.StackedInline):
    model = Video
    extra = 1
    fields = ('title', 'url', 'duration', 'description', 'order')

class ResourceInline(SortableInlineAdminMixin, admin.StackedInline):
    model = Resource
    extra = 1
    fields = ('title', 'resource_type', 'url', 'description', 'order')

class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 4

class QuestionInline(SortableInlineAdminMixin, admin.StackedInline):
    model = Question
    extra = 1
    fields = ('text', 'order')
    show_change_link = True

@admin.register(Topic)
class TopicAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'subject', 'order', 'question_count_display', 'is_active', 'passing_score_override')
    list_filter = ('subject', 'is_active')
    search_fields = ('name', 'summary')
    inlines = [VideoInline, ResourceInline, QuestionInline]

    def question_count_display(self, obj):
        count = obj.questions.count()
        status = "Ready" if obj.is_assessment_ready() else "Pending Questions"
        return f"{count} questions ({status})"
    question_count_display.short_description = "Assessment Status"

@admin.register(Video)
class VideoAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'topic', 'duration', 'order')
    list_filter = ('topic__subject', 'topic')
    search_fields = ('title', 'description')

@admin.register(Resource)
class ResourceAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'topic', 'resource_type', 'order')
    list_filter = ('resource_type', 'topic__subject', 'topic')
    search_fields = ('title', 'description')
