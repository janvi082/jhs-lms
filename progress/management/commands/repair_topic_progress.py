from django.core.management.base import BaseCommand
from django.db import transaction
from progress.models import TopicProgress
from quizzes.models import QuizAttempt

class Command(BaseCommand):
    help = 'Repairs historical TopicProgress records.'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true', help='Execute the repair and save to database.')

    def handle(self, *args, **options):
        execute = options['execute']

        total_scanned = 0
        records_requiring_repair = 0
        attempts_count_fixes = 0
        best_score_fixes = 0
        latest_score_fixes = 0
        status_upgrades = 0
        completed_at_backfills = 0

        try:
            with transaction.atomic():
                for tp in TopicProgress.objects.all().select_related('user', 'topic'):
                    total_scanned += 1

                    # Fetch all related fully graded QuizAttempts
                    attempts = list(tp.topic.quiz_attempts.filter(user=tp.user).order_by('created_at'))
                    new_attempts_count = len(attempts)

                    fully_graded_attempts = []
                    for attempt in attempts:
                        if not attempt.responses.filter(is_correct__isnull=True).exists():
                            fully_graded_attempts.append(attempt)

                    new_best_score = tp.best_score
                    new_latest_score = tp.latest_score
                    if fully_graded_attempts:
                        new_best_score = max([a.score for a in fully_graded_attempts])
                        new_latest_score = fully_graded_attempts[-1].score

                    new_status = tp.status
                    new_completed_at = tp.completed_at

                    if fully_graded_attempts:
                        any_passed = any([a.passed for a in fully_graded_attempts])
                        if any_passed and tp.status != TopicProgress.STATUS_COMPLETED:
                            new_status = TopicProgress.STATUS_COMPLETED
                            if tp.completed_at is None:
                                passing_attempts = [a for a in fully_graded_attempts if a.passed]
                                new_completed_at = passing_attempts[0].created_at

                    changes = []
                    if tp.attempts_count != new_attempts_count:
                        changes.append(('attempts_count', tp.attempts_count, new_attempts_count))
                        attempts_count_fixes += 1
                    if tp.best_score != new_best_score:
                        changes.append(('best_score', tp.best_score, new_best_score))
                        best_score_fixes += 1
                    if tp.latest_score != new_latest_score:
                        changes.append(('latest_score', tp.latest_score, new_latest_score))
                        latest_score_fixes += 1
                    if tp.status != new_status:
                        changes.append(('status', tp.status, new_status))
                        status_upgrades += 1
                    if tp.completed_at != new_completed_at:
                        changes.append(('completed_at', tp.completed_at, new_completed_at))
                        completed_at_backfills += 1

                    if changes:
                        records_requiring_repair += 1

                        prefix = "[DRY RUN] Would update" if not execute else "[EXECUTE] Updating"
                        self.stdout.write(f"{prefix} Learner {tp.user.id}, Topic {tp.topic.id} ({tp.topic.name}):")
                        for field, old_val, new_val in changes:
                            self.stdout.write(f"  - {field}: {old_val} -> {new_val}")

                        if execute:
                            tp.attempts_count = new_attempts_count
                            tp.best_score = new_best_score
                            tp.latest_score = new_latest_score
                            tp.status = new_status
                            tp.completed_at = new_completed_at
                            tp.save(update_fields=['attempts_count', 'best_score', 'latest_score', 'status', 'completed_at'])

                if not execute:
                    # Rollback just in case, though we didn't save
                    transaction.set_rollback(True)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error occurred: {e}"))
            raise

        self.stdout.write("\n--- SUMMARY ---")
        self.stdout.write(f"Total records scanned: {total_scanned}")
        self.stdout.write(f"Records requiring repair: {records_requiring_repair}")
        self.stdout.write(f"  attempts_count fixes: {attempts_count_fixes}")
        self.stdout.write(f"  best_score fixes: {best_score_fixes}")
        self.stdout.write(f"  latest_score fixes: {latest_score_fixes}")
        self.stdout.write(f"  status upgrades: {status_upgrades}")
        self.stdout.write(f"  completed_at backfills: {completed_at_backfills}")

        if execute:
            self.stdout.write("DATABASE UPDATED SUCCESSFULLY.")
        else:
            self.stdout.write("DATABASE UNTOUCHED.")
