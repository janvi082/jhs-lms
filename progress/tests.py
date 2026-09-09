from django.test import TestCase
from accounts.models import User
from content.models import SiteConfig, Subject, Topic, Video, Resource
from quizzes.models import Question, Choice, QuizAttempt
from progress.models import TopicProgress
from progress.services import (
    record_topic_view,
    get_topic_display_status,
    get_subject_progress_summary,
    get_overall_learner_progress,
    submit_quiz_attempt
)

class LMSCoreTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        self.learner = User.objects.create_user(
            username='testlearner',
            password='password123',
            role=User.ROLE_LEARNER
        )

        self.subject = Subject.objects.create(
            name="Communication Skills",
            description="Dynamic subject test",
            order=1,
            is_active=True
        )

        self.topic1 = Topic.objects.create(
            subject=self.subject,
            name="Active Listening",
            summary="Learn effective listening skills.",
            order=1,
            is_active=True
        )

        self.topic2 = Topic.objects.create(
            subject=self.subject,
            name="Public Speaking",
            summary="Learn stage confidence.",
            order=2,
            is_active=True
        )

        # Create 5 questions for topic1
        for i in range(1, 6):
            q = Question.objects.create(topic=self.topic1, text=f"Question {i}", order=i)
            Choice.objects.create(question=q, text="Option A (Correct)", is_correct=True)
            Choice.objects.create(question=q, text="Option B", is_correct=False)
            Choice.objects.create(question=q, text="Option C", is_correct=False)
            Choice.objects.create(question=q, text="Option D", is_correct=False)

    def test_topic_view_flips_status_to_in_progress(self):
        tp = record_topic_view(self.learner, self.topic1)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNotNone(tp.last_accessed)
        disp = get_topic_display_status(tp)
        self.assertEqual(disp['code'], 'in_progress')

    def test_quiz_submit_pass_and_fail(self):
        # 1. Failed Attempt (1/5 correct = 20%)
        q1 = self.topic1.questions.first()
        wrong_choice = q1.choices.filter(is_correct=False).first()
        answers_wrong = {q.id: wrong_choice.id for q in self.topic1.questions.all()}
        
        att1, tp1 = submit_quiz_attempt(self.learner, self.topic1, answers_wrong)
        self.assertFalse(att1.passed)
        self.assertEqual(att1.score, 0)
        self.assertEqual(tp1.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertEqual(tp1.attempts_count, 1)
        
        disp_wrong = get_topic_display_status(tp1)
        self.assertEqual(disp_wrong['code'], 'needs_improvement')

        # 2. Passed Attempt (5/5 correct = 100%)
        answers_correct = {}
        for q in self.topic1.questions.all():
            corr = q.choices.get(is_correct=True)
            answers_correct[q.id] = corr.id

        att2, tp2 = submit_quiz_attempt(self.learner, self.topic1, answers_correct)
        self.assertTrue(att2.passed)
        self.assertEqual(att2.score, 100)
        self.assertEqual(tp2.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp2.attempts_count, 2)
        self.assertEqual(tp2.best_score, 100)
        self.assertIsNotNone(tp2.completed_at)

        disp_pass = get_topic_display_status(tp2)
        self.assertEqual(disp_pass['code'], 'completed')

    def test_subject_progress_and_understanding_calculations(self):
        # Topic 1 completed (100%), Topic 2 not attempted (0%)
        # Subject progress should be 1/2 = 50%
        answers_correct = {q.id: q.choices.get(is_correct=True).id for q in self.topic1.questions.all()}
        submit_quiz_attempt(self.learner, self.topic1, answers_correct)

        summary = get_subject_progress_summary(self.learner, self.subject)
        self.assertEqual(summary['progress_percent'], 50)
        self.assertEqual(summary['understanding_percent'], 100)
        self.assertEqual(summary['completed_topics_count'], 1)
        self.assertEqual(summary['total_topics_count'], 2)

        overall = get_overall_learner_progress(self.learner)
        self.assertEqual(overall, 50)

    def test_dynamic_brand_new_subject_without_code_changes(self):
        """
        Verifies that a brand-new subject created dynamically via ORM works 100% identically.
        """
        new_sub = Subject.objects.create(name="Cyber Security", order=99)
        new_topic = Topic.objects.create(subject=new_sub, name="Password Hygiene", summary="Summary", order=1)
        for i in range(1, 6):
            q = Question.objects.create(topic=new_topic, text=f"Q{i}", order=i)
            Choice.objects.create(question=q, text="Yes", is_correct=True)
            Choice.objects.create(question=q, text="No", is_correct=False)

        self.assertTrue(new_topic.is_assessment_ready())
        answers = {q.id: q.choices.get(is_correct=True).id for q in new_topic.questions.all()}
        att, tp = submit_quiz_attempt(self.learner, new_topic, answers)
        self.assertTrue(att.passed)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
