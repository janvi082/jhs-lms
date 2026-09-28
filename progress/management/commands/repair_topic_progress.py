from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q
from django.contrib.auth import get_user_model
from progress.models import TopicProgress
from progress.services import calculate_reconciled_fields
from content.models import Topic
from quizzes.models import QuizAttempt
from collections import defaultdict
from itertools import islice

User = get_user_model()

def chunked_iterable(iterable, size):
    it = iter(iterable)
    while True:
        chunk = tuple(islice(it, size))
        if not chunk:
            break
        yield chunk

class Command(BaseCommand):
    help = 'Repairs historical TopicProgress records using bulk loading and canonical helper.'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true', help='Execute the repair and save to database.')

    def handle(self, *args, **options):
        execute = options['execute']

        # 1. Discover unique user/topic combinations
        combinations = set()
        for tp in TopicProgress.objects.all().values_list('user_id', 'topic_id'):
            combinations.add(tp)
        for qa in QuizAttempt.objects.all().values_list('user_id', 'topic_id'):
            combinations.add(qa)
            
        combinations = list(combinations)
            
        total_scanned = len(combinations)
        records_requiring_repair = 0
        missing_records_created = 0
        already_consistent = 0

        attempts_count_fixes = 0
        best_score_fixes = 0
        latest_score_fixes = 0
        status_fixes = 0
        completed_at_fixes = 0

        try:
            with transaction.atomic():
                for chunk in chunked_iterable(combinations, 1000):
                    user_ids = {u for u, t in chunk}
                    topic_ids = {t for u, t in chunk}
                    
                    users_bulk = User.objects.in_bulk(user_ids)
                    topics_bulk = Topic.objects.in_bulk(topic_ids)
                    
                    q_objects = Q()
                    for u_id, t_id in chunk:
                        q_objects |= Q(user_id=u_id, topic_id=t_id)
                        
                    existing_tps = {
                        (tp.user_id, tp.topic_id): tp 
                        for tp in TopicProgress.objects.filter(q_objects)
                    }
                    
                    attempts_qs = QuizAttempt.objects.filter(q_objects).annotate(
                        ungraded_count=Count('responses', filter=Q(responses__is_correct__isnull=True))
                    ).order_by('created_at', 'id')
                    
                    grouped_attempts = defaultdict(list)
                    for a in attempts_qs:
                        grouped_attempts[(a.user_id, a.topic_id)].append({
                            'score': a.score,
                            'passed': a.passed,
                            'created_at': a.created_at,
                            'is_fully_graded': a.ungraded_count == 0
                        })
                        
                    for u_id, t_id in chunk:
                        # Skip if DB is inconsistent and objects are missing
                        if u_id not in users_bulk or t_id not in topics_bulk:
                            continue
                            
                        user = users_bulk[u_id]
                        topic = topics_bulk[t_id]
                        old_tp = existing_tps.get((u_id, t_id))
                        is_missing = old_tp is None
                        
                        attempts_data = grouped_attempts.get((u_id, t_id), [])
                        
                        current_status = old_tp.status if not is_missing else TopicProgress.STATUS_NOT_STARTED
                        current_completed_at = old_tp.completed_at if not is_missing else None
                        
                        result = calculate_reconciled_fields(
                            topic_assessment_required=topic.assessment_required,
                            attempts_data=attempts_data,
                            current_status=current_status,
                            current_completed_at=current_completed_at
                        )
                        
                        changes = []
                        old_attempts_count = old_tp.attempts_count if not is_missing else None
                        old_best_score = old_tp.best_score if not is_missing else None
                        old_latest_score = old_tp.latest_score if not is_missing else None
                        old_status = current_status if not is_missing else None
                        old_completed_at = current_completed_at if not is_missing else None

                        if is_missing or old_attempts_count != result['attempts_count']:
                            changes.append(('attempts_count', old_attempts_count, result['attempts_count']))
                            attempts_count_fixes += 1
                        if is_missing or old_best_score != result['best_score']:
                            changes.append(('best_score', old_best_score, result['best_score']))
                            best_score_fixes += 1
                        if is_missing or old_latest_score != result['latest_score']:
                            changes.append(('latest_score', old_latest_score, result['latest_score']))
                            latest_score_fixes += 1
                        if is_missing or old_status != result['status']:
                            changes.append(('status', old_status, result['status']))
                            status_fixes += 1
                        if is_missing or old_completed_at != result['completed_at']:
                            changes.append(('completed_at', old_completed_at, result['completed_at']))
                            completed_at_fixes += 1

                        if is_missing:
                            missing_records_created += 1

                        if changes:
                            records_requiring_repair += 1
                            prefix = "[DRY RUN] Would update" if not execute else "[EXECUTE] Updating"
                            if is_missing:
                                prefix = "[DRY RUN] Would create missing" if not execute else "[EXECUTE] Creating missing"
                            
                            self.stdout.write(f"{prefix} Learner {user.id}, Topic {topic.id} ({topic.name}):")
                            for field, old_val, new_val in changes:
                                self.stdout.write(f"  - {field}: {old_val} -> {new_val}")
                                
                            if execute:
                                if is_missing:
                                    TopicProgress.objects.create(
                                        user=user,
                                        topic=topic,
                                        attempts_count=result['attempts_count'],
                                        best_score=result['best_score'],
                                        latest_score=result['latest_score'],
                                        status=result['status'],
                                        completed_at=result['completed_at']
                                    )
                                else:
                                    old_tp.attempts_count = result['attempts_count']
                                    old_tp.best_score = result['best_score']
                                    old_tp.latest_score = result['latest_score']
                                    old_tp.status = result['status']
                                    old_tp.completed_at = result['completed_at']
                                    old_tp.save(update_fields=['attempts_count', 'best_score', 'latest_score', 'status', 'completed_at'])
                        else:
                            already_consistent += 1

                if not execute:
                    # Rollback just in case
                    transaction.set_rollback(True)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error occurred: {e}"))
            raise

        self.stdout.write("\n--- SUMMARY ---")
        self.stdout.write(f"Combinations scanned: {total_scanned}")
        self.stdout.write(f"Missing TopicProgress records: {missing_records_created}")
        self.stdout.write(f"Records requiring changes: {records_requiring_repair}")
        self.stdout.write(f"Records already consistent: {already_consistent}")
        
        self.stdout.write(f"\n  attempts_count fixes: {attempts_count_fixes}")
        self.stdout.write(f"  best_score fixes: {best_score_fixes}")
        self.stdout.write(f"  latest_score fixes: {latest_score_fixes}")
        self.stdout.write(f"  status fixes: {status_fixes}")
        self.stdout.write(f"  completed_at fixes: {completed_at_fixes}")

        if execute:
            self.stdout.write("\nDATABASE UPDATED SUCCESSFULLY.")
        else:
            self.stdout.write("\nDATABASE UNTOUCHED.")
