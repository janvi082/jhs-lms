from django.contrib import admin
from django.forms.models import BaseInlineFormSet
from django.core.exceptions import ValidationError
from adminsortable2.admin import SortableAdminMixin
from .models import Question, Choice, QuizAttempt, QuizResponse

class ChoiceInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        q_type = None
        if hasattr(self, 'instance') and self.instance:
            q_type = self.instance.question_type

        correct_count = 0
        total_choices = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            total_choices += 1
            if form.cleaned_data.get('is_correct'):
                correct_count += 1
                
        if total_choices > 0:
            if q_type in (Question.TYPE_SINGLE_CHOICE, Question.TYPE_TRUE_FALSE) and correct_count != 1:
                raise ValidationError("Single Choice and True/False questions must have exactly one correct answer.")
            elif q_type == Question.TYPE_MULTIPLE_CHOICE and correct_count < 1:
                raise ValidationError("Multiple Choice questions must have at least one correct answer.")

class ChoiceInline(admin.TabularInline):
    model = Choice
    formset = ChoiceInlineFormSet
    extra = 4

@admin.register(Question)
class QuestionAdmin(SortableAdminMixin, admin.ModelAdmin):
    list_display = ('text_truncated', 'topic', 'question_type', 'required', 'order', 'choices_count')
    list_filter = ('question_type', 'required', 'topic__subject', 'topic')
    search_fields = ('text', 'accepted_answers')
    inlines = [ChoiceInline]

    def text_truncated(self, obj):
        return obj.text[:80]
    text_truncated.short_description = "Question"

    def choices_count(self, obj):
        return obj.choices.count()
    choices_count.short_description = "Choices"


class QuizResponseInline(admin.TabularInline):
    model = QuizResponse
    extra = 0
    readonly_fields = ('question', 'selected_choices_display', 'text_response', 'is_correct', 'created_at')
    fields = ('question', 'selected_choices_display', 'text_response', 'is_correct', 'created_at')
    can_delete = False

    def selected_choices_display(self, obj):
        choices = obj.selected_choices.all()
        if not choices.exists():
            return "-"
        return ", ".join([c.text for c in choices])
    selected_choices_display.short_description = "Selected Choices"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'topic', 'attempt_number', 'score', 'passed', 'passing_score_used', 'created_at')
    list_filter = ('passed', 'topic__subject', 'created_at')
    search_fields = ('user__username', 'topic__name')
    readonly_fields = ('user', 'topic', 'attempt_number', 'correct_count', 'total_questions', 'score', 'passed', 'passing_score_used', 'created_at')
    inlines = [QuizResponseInline]

    def has_add_permission(self, request):
        return False
