from django.test import TestCase, Client
from django.urls import reverse
from django.db.utils import IntegrityError
from accounts.models import User
from content.models import SiteConfig, Subject, Topic
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.services import submit_quiz_attempt

class QuizSubmissionFlowTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        self.learner = User.objects.create_user(
            username='learner1',
            password='password123',
            role=User.ROLE_LEARNER
        )

        self.subject = Subject.objects.create(
            name="Excel Mastery",
            slug="excel",
            description="Excel course",
            order=1,
            is_active=True
        )

        self.topic = Topic.objects.create(
            subject=self.subject,
            name="Formulas 101",
            summary="Basic formulas",
            order=1,
            is_active=True
        )

        # Create 5 questions with 1 correct choice each
        self.questions = []
        for i in range(1, 6):
            q = Question.objects.create(
                topic=self.topic,
                text=f"Question {i}",
                question_type=Question.TYPE_SINGLE_CHOICE,
                required=False,
                order=i
            )
            c1 = Choice.objects.create(question=q, text=f"Correct Choice {i}", is_correct=True)
            c2 = Choice.objects.create(question=q, text=f"Wrong Choice {i}", is_correct=False)
            self.questions.append((q, c1, c2))

        self.client = Client()
        self.client.login(username='learner1', password='password123')
        self.quiz_url = reverse('quiz_modal', kwargs={'slug': self.subject.slug, 'topic_id': self.topic.id})

    def test_quiz_get_loads_assessment_page_without_answers_in_url(self):
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<form method="post"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'Submit Assessment')

    def test_quiz_post_creates_attempt_and_redirects_to_result_page(self):
        post_data = {str(q.id): corr.id for q, corr, wrong in self.questions}
        
        self.client.get(self.quiz_url)
        response = self.client.post(self.quiz_url, post_data)
        self.assertEqual(response.status_code, 302)
        
        attempts = QuizAttempt.objects.filter(user=self.learner, topic=self.topic)
        self.assertEqual(attempts.count(), 1)
        attempt = attempts.first()
        self.assertEqual(attempt.score, 100)
        self.assertEqual(attempt.correct_count, 5)
        self.assertEqual(attempt.total_questions, 5)
        self.assertTrue(attempt.passed)

        # Verify QuizResponse records created permanently
        responses = QuizResponse.objects.filter(attempt=attempt)
        self.assertEqual(responses.count(), 5)
        for resp in responses:
            self.assertTrue(resp.is_correct)

        # Follow redirect to result page
        result_url = response.url
        res_page = self.client.get(result_url)
        self.assertEqual(res_page.status_code, 200)
        self.assertContains(res_page, '100%')
        self.assertContains(res_page, 'PASS')
        self.assertContains(res_page, 'Question Review')

    def test_refreshing_result_page_does_not_create_duplicate_attempt(self):
        post_data = {str(q.id): corr.id for q, corr, wrong in self.questions}
        self.client.get(self.quiz_url)
        response = self.client.post(self.quiz_url, post_data)
        result_url = response.url

        self.assertEqual(QuizAttempt.objects.count(), 1)

        self.client.get(result_url)
        self.client.get(result_url)
        self.assertEqual(QuizAttempt.objects.count(), 1)

    def test_skipped_count_calculation_and_display(self):
        # 1. Fully answered attempt
        post_data_full = {str(q.id): corr.id for q, corr, wrong in self.questions}
        self.client.get(self.quiz_url)
        res_full = self.client.post(self.quiz_url, post_data_full, follow=True)
        self.assertEqual(res_full.context['skipped_count'], 0)
        self.assertContains(res_full, '<div class="fw-bold fs-5">0</div>')
        
        # Reset attempts
        QuizAttempt.objects.all().delete()
        
        # 2. Partially answered attempt (answer 2, skip 3)
        post_data_partial = {
            str(self.questions[0][0].id): self.questions[0][1].id,
            str(self.questions[1][0].id): self.questions[1][2].id,
        }
        self.client.get(self.quiz_url)
        res_partial = self.client.post(self.quiz_url, post_data_partial, follow=True)
        self.assertEqual(res_partial.context['skipped_count'], 3)
        self.assertContains(res_partial, '<div class="fw-bold fs-5">3</div>')
        
        # Reset
        QuizAttempt.objects.all().delete()
        
        # 3. Fully blank attempt
        self.client.get(self.quiz_url)
        res_blank = self.client.post(self.quiz_url, {}, follow=True)
        self.assertEqual(res_blank.context['skipped_count'], 5)
        self.assertContains(res_blank, '<div class="fw-bold fs-5">5</div>')
        
        # Reset
        QuizAttempt.objects.all().delete()
        
        # 4. Optional paragraph question
        q_para = Question.objects.create(
            topic=self.topic,
            text="Paragraph Question",
            question_type=Question.TYPE_PARAGRAPH,
            required=False,
            order=6
        )
        self.client.get(self.quiz_url)
        res_para = self.client.post(self.quiz_url, post_data_full, follow=True)
        # We answered 5, left 1 optional paragraph blank. So 1 skipped.
        self.assertEqual(res_para.context['skipped_count'], 1)
        self.assertContains(res_para, '<div class="fw-bold fs-5">1</div>')

    def test_empty_post_handled_safely(self):
        self.client.get(self.quiz_url)
        response = self.client.post(self.quiz_url, {})
        self.assertEqual(response.status_code, 302)
        attempt = QuizAttempt.objects.first()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.score, 0)
        self.assertFalse(attempt.passed)
        self.assertEqual(attempt.correct_count, 0)


class QuestionTypesAndGradingTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create_user(username='teststudent', password='password123')
        self.subject = Subject.objects.create(name="Accounting", slug="accounting", is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name="Tax Basics", is_active=True)

        # 1. Single Choice
        self.q_single = Question.objects.create(
            topic=self.topic, text="What is 2+2?", question_type=Question.TYPE_SINGLE_CHOICE, required=False, order=1
        )
        self.c_single_correct = Choice.objects.create(question=self.q_single, text="4", is_correct=True)
        self.c_single_wrong = Choice.objects.create(question=self.q_single, text="5", is_correct=False)

        # 2. Multiple Choice
        self.q_multi = Question.objects.create(
            topic=self.topic, text="Which are accounting standards?", question_type=Question.TYPE_MULTIPLE_CHOICE, required=False, order=2
        )
        self.c_multi_c1 = Choice.objects.create(question=self.q_multi, text="IFRS", is_correct=True)
        self.c_multi_c2 = Choice.objects.create(question=self.q_multi, text="GAAP", is_correct=True)
        self.c_multi_w1 = Choice.objects.create(question=self.q_multi, text="HTML", is_correct=False)

        # 3. True / False
        self.q_tf = Question.objects.create(
            topic=self.topic, text="Assets = Liabilities + Equity", question_type=Question.TYPE_TRUE_FALSE, required=False, order=3
        )
        self.c_tf_true = Choice.objects.create(question=self.q_tf, text="True", is_correct=True)
        self.c_tf_false = Choice.objects.create(question=self.q_tf, text="False", is_correct=False)

        # 4. Short Answer
        self.q_short = Question.objects.create(
            topic=self.topic,
            text="What is the full form of VAT?",
            question_type=Question.TYPE_SHORT_ANSWER,
            accepted_answers="Value Added Tax\nVAT\nvalue-added tax",
            required=False, order=4
        )

        # 5. Paragraph
        self.q_para = Question.objects.create(
            topic=self.topic,
            text="Explain current vs non-current assets.",
            question_type=Question.TYPE_PARAGRAPH,
            required=False, order=5
        )

    def test_single_choice_grading(self):
        submitted = {self.q_single.id: self.c_single_correct.id}
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submitted)
        resp = attempt.responses.get(question=self.q_single)
        self.assertTrue(resp.is_correct)

    def test_multiple_choice_exact_match_grading(self):
        # Exact correct set -> Correct
        submitted = {
            self.q_multi.id: [self.c_multi_c1.id, self.c_multi_c2.id]
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submitted)
        resp = attempt.responses.get(question=self.q_multi)
        self.assertTrue(resp.is_correct)

        # Partial correct set -> Incorrect (all-or-nothing)
        submitted_partial = {
            self.q_multi.id: [self.c_multi_c1.id]
        }
        attempt2, _ = submit_quiz_attempt(self.learner, self.topic, submitted_partial)
        resp2 = attempt2.responses.get(question=self.q_multi)
        self.assertFalse(resp2.is_correct)

        # Extra incorrect item -> Incorrect
        submitted_extra = {
            self.q_multi.id: [self.c_multi_c1.id, self.c_multi_c2.id, self.c_multi_w1.id]
        }
        attempt3, _ = submit_quiz_attempt(self.learner, self.topic, submitted_extra)
        resp3 = attempt3.responses.get(question=self.q_multi)
        self.assertFalse(resp3.is_correct)

    def test_short_answer_case_insensitive_and_whitespace_trimming(self):
        test_inputs = ["VAT", "vat", " VAT ", "Value Added Tax", "value-added tax"]
        for inp in test_inputs:
            attempt, _ = submit_quiz_attempt(self.learner, self.topic, {self.q_short.id: inp})
            resp = attempt.responses.get(question=self.q_short)
            self.assertTrue(resp.is_correct, f"Failed for input '{inp}'")

    def test_paragraph_excluded_from_auto_scoring(self):
        # Submit correct answers for 4 gradable questions, and text for 1 paragraph question
        submitted = {
            self.q_single.id: self.c_single_correct.id,
            self.q_multi.id: [self.c_multi_c1.id, self.c_multi_c2.id],
            self.q_tf.id: self.c_tf_true.id,
            self.q_short.id: "VAT",
            self.q_para.id: "Current assets are expected to be converted to cash within a year..."
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submitted)
        
        # 4 out of 4 gradable questions correct -> 100% (NOT 4/5 = 80%)
        self.assertEqual(attempt.score, 100)
        self.assertEqual(attempt.correct_count, 4)
        self.assertEqual(attempt.total_questions, 5)

        para_resp = attempt.responses.get(question=self.q_para)
        self.assertIsNone(para_resp.is_correct)
        self.assertIn("Current assets", para_resp.text_response)

    def test_unique_attempt_question_constraint(self):
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, {self.q_single.id: self.c_single_correct.id})
        with self.assertRaises(IntegrityError):
            QuizResponse.objects.create(attempt=attempt, question=self.q_single, is_correct=True)

