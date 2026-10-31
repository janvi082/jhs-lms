from django.contrib import admin
from .models import TopicProgress, TopicProgressTimingAudit

@admin.register(TopicProgress)
class TopicProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'topic', 'status', 'best_score', 'latest_score', 'attempts_count', 'last_accessed', 'completed_at')
    list_filter = ('status', 'topic__subject')
    search_fields = ('user__username', 'topic__name')


@admin.register(TopicProgressTimingAudit)
class TopicProgressTimingAuditAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'action', 'original_topic_progress_id', 'repair_batch_id',
        'before_frozen_deadline', 'after_frozen_deadline',
        'before_timing_provenance', 'after_timing_provenance',
        'actor', 'created_at'
    )
    list_filter = ('action', 'created_at')
    search_fields = ('repair_batch_id', 'reason', 'original_topic_progress_id')
    readonly_fields = [
        'topic_progress', 'original_topic_progress_id', 'reverted_audit_entry',
        'repair_batch_id', 'action', 'reason',
        'before_expected_end_day_snapshot', 'after_expected_end_day_snapshot',
        'before_frozen_deadline', 'after_frozen_deadline',
        'before_timing_provenance', 'after_timing_provenance',
        'before_status', 'after_status',
        'before_completed_at', 'after_completed_at',
        'actor', 'created_at'
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
