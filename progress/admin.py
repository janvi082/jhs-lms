from django.contrib import admin
from .models import TopicProgress

@admin.register(TopicProgress)
class TopicProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'topic', 'status', 'best_score', 'latest_score', 'attempts_count', 'last_accessed', 'completed_at')
    list_filter = ('status', 'topic__subject')
    search_fields = ('user__username', 'topic__name')
