from django.contrib import admin
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import ValidationError
from adminsortable2.admin import SortableAdminMixin
from .models import Question, Choice, QuizAttempt

class ChoiceInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        correct_count = 0
        total_choices = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            total_choices += 1
            if form.cleaned_data.get('is_correct'):
                correct_count += 1
                
        if total_choices > 0:
            if correct_count != 1:
                raise ValidationError("Each question must have exactly one correct answer (marked as is_correct).")

class ChoiceInline(admin.TabularInline):
    model = Choice
    formset = ChoiceInlineFormSet
    extra = 4

@admin.register(Question)
class QuestionAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('text_truncated', 'topic', 'order', 'choices_count')
    list_filter = ('topic__subject', 'topic')
    search_fields = ('text',)
    inlines = [ChoiceInline]

    def text_truncated(self, obj):
        return obj.text[:80]
    text_truncated.short_description = "Question"

    def choices_count(self, obj):
        return obj.choices.count()
    choices_count.short_description = "Choices"

@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'topic', 'attempt_number', 'score', 'passed', 'passing_score_used', 'created_at')
    list_filter = ('passed', 'topic__subject', 'created_at')
    search_fields = ('user__username', 'topic__name')
    readonly_fields = ('user', 'topic', 'attempt_number', 'correct_count', 'total_questions', 'score', 'passed', 'passing_score_used', 'created_at')

    def has_add_permission(self, request):
        return False
