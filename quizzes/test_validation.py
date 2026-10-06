from django.test import TestCase
from django.contrib.auth import get_user_model
from content.models import Subject, Topic
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from progress.services import submit_quiz_attempt
from django.core.exceptions import ValidationError

User = get_user_model()

class QuizValidationTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create_user(username='test_learner_val', password='password123')
        self.subject = Subject.objects.create(name='Validation Subject', is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name='Validation Topic', is_active=True, assessment_required=True)
        
        self.q_single = Question.objects.create(topic=self.topic, text='Q1 Single', question_type=Question.TYPE_SINGLE_CHOICE, required=True, order=1)
        self.c_single = Choice.objects.create(question=self.q_single, text='C1', is_correct=True)
        
        self.q_multi = Question.objects.create(topic=self.topic, text='Q2 Multi', question_type=Question.TYPE_MULTIPLE_CHOICE, required=True, order=2)
        self.c_multi = Choice.objects.create(question=self.q_multi, text='C2', is_correct=True)
        
        self.q_tf = Question.objects.create(topic=self.topic, text='Q3 TF', question_type=Question.TYPE_TRUE_FALSE, required=True, order=3)
        self.c_tf = Choice.objects.create(question=self.q_tf, text='True', is_correct=True)
        
        self.q_short = Question.objects.create(topic=self.topic, text='Q4 Short', question_type=Question.TYPE_SHORT_ANSWER, required=True, accepted_answers='ans', order=4)
        
        self.q_para = Question.objects.create(topic=self.topic, text='Q5 Para', question_type=Question.TYPE_PARAGRAPH, required=True, order=5)

    def test_missing_required_single_choice(self):
        with self.assertRaisesMessage(ValidationError, "Question 'Q1 Single' is required."):
            submit_quiz_attempt(self.learner, self.topic, {
                str(self.q_multi.id): [self.c_multi.id],
                str(self.q_tf.id): self.c_tf.id,
                str(self.q_short.id): 'ans',
                str(self.q_para.id): 'para'
            })

    def test_missing_required_multiple_choice(self):
        with self.assertRaisesMessage(ValidationError, "Question 'Q2 Multi' is required."):
            submit_quiz_attempt(self.learner, self.topic, {
                str(self.q_single.id): self.c_single.id,
                str(self.q_tf.id): self.c_tf.id,
                str(self.q_short.id): 'ans',
                str(self.q_para.id): 'para'
            })

    def test_missing_required_true_false(self):
        with self.assertRaisesMessage(ValidationError, "Question 'Q3 TF' is required."):
            submit_quiz_attempt(self.learner, self.topic, {
                str(self.q_single.id): self.c_single.id,
                str(self.q_multi.id): [self.c_multi.id],
                str(self.q_short.id): 'ans',
                str(self.q_para.id): 'para'
            })

    def test_missing_required_short_answer_whitespace(self):
        with self.assertRaisesMessage(ValidationError, "Question 'Q4 Short' is required."):
            submit_quiz_attempt(self.learner, self.topic, {
                str(self.q_single.id): self.c_single.id,
                str(self.q_multi.id): [self.c_multi.id],
                str(self.q_tf.id): self.c_tf.id,
                str(self.q_short.id): '   ',
                str(self.q_para.id): 'para'
            })

    def test_missing_required_paragraph_whitespace(self):
        with self.assertRaisesMessage(ValidationError, "Question 'Q5 Para' is required."):
            submit_quiz_attempt(self.learner, self.topic, {
                str(self.q_single.id): self.c_single.id,
                str(self.q_multi.id): [self.c_multi.id],
                str(self.q_tf.id): self.c_tf.id,
                str(self.q_short.id): 'ans',
                str(self.q_para.id): '   '
            })
            
    def test_invalid_required_creates_no_records(self):
        initial_attempts = QuizAttempt.objects.count()
        initial_responses = QuizResponse.objects.count()
        
        try:
            submit_quiz_attempt(self.learner, self.topic, {})
        except ValidationError:
            pass
            
        self.assertEqual(QuizAttempt.objects.count(), initial_attempts)
        self.assertEqual(QuizResponse.objects.count(), initial_responses)

    def test_unanswered_optional_questions_do_not_block(self):
        self.q_single.required = False
        self.q_single.save()
        self.q_multi.required = False
        self.q_multi.save()
        self.q_tf.required = False
        self.q_tf.save()
        self.q_short.required = False
        self.q_short.save()
        self.q_para.required = False
        self.q_para.save()
        
        attempt, progress = submit_quiz_attempt(self.learner, self.topic, {})
        self.assertIsNotNone(attempt)
        # 0 responses created because all are optional and unanswered
        self.assertEqual(attempt.responses.count(), 0)

    def test_skipped_optional_short_answer_no_gradable_count(self):
        self.q_short.required = False
        self.q_short.save()
        
        # answer other questions so an attempt is made
        attempt, progress = submit_quiz_attempt(self.learner, self.topic, {
            str(self.q_single.id): self.c_single.id,
            str(self.q_multi.id): [self.c_multi.id],
            str(self.q_tf.id): self.c_tf.id,
            str(self.q_para.id): 'para'
        })
        self.assertEqual(attempt.responses.filter(question=self.q_short).count(), 0)
        # Should not count against score
        self.assertEqual(attempt.total_questions, 5)
        # gradable should be 3 (single, multi, TF). Short answer and paragraph are skipped/not auto-graded
        # So score should be 100
        self.assertEqual(attempt.score, 100)

    def test_skipped_optional_paragraph_no_pending_review(self):
        self.q_para.required = False
        self.q_para.save()
        
        attempt, progress = submit_quiz_attempt(self.learner, self.topic, {
            str(self.q_single.id): self.c_single.id,
            str(self.q_multi.id): [self.c_multi.id],
            str(self.q_tf.id): self.c_tf.id,
            str(self.q_short.id): 'ans',
        })
        self.assertEqual(attempt.responses.filter(question=self.q_para).count(), 0)
        
        from progress.services import annotate_progress_with_review_status
        annotated = annotate_progress_with_review_status(TopicProgress.objects.filter(id=progress.id)).first()
        self.assertFalse(annotated.has_pending_review)
        self.assertTrue(annotated.has_finalized_score)

    def test_answered_optional_retains_behavior(self):
        self.q_short.required = False
        self.q_short.save()
        
        # Wrong answer
        attempt, progress = submit_quiz_attempt(self.learner, self.topic, {
            str(self.q_single.id): self.c_single.id,
            str(self.q_multi.id): [self.c_multi.id],
            str(self.q_tf.id): self.c_tf.id,
            str(self.q_short.id): 'wrongans',
            str(self.q_para.id): 'para'
        })
        resp = attempt.responses.get(question=self.q_short)
        self.assertFalse(resp.is_correct)
        
    def test_mixed_quiz_submits_successfully(self):
        self.q_short.required = False
        self.q_short.save()
        self.q_para.required = False
        self.q_para.save()
        
        attempt, progress = submit_quiz_attempt(self.learner, self.topic, {
            str(self.q_single.id): self.c_single.id,
            str(self.q_multi.id): [self.c_multi.id],
            str(self.q_tf.id): self.c_tf.id,
        })
        self.assertIsNotNone(attempt)

    def test_mixed_quiz_rejected_on_required_missing(self):
        self.q_short.required = False
        self.q_short.save()
        
        with self.assertRaisesMessage(ValidationError, "Question 'Q1 Single' is required."):
            submit_quiz_attempt(self.learner, self.topic, {
                str(self.q_multi.id): [self.c_multi.id],
                str(self.q_tf.id): self.c_tf.id,
                # short answer skipped (optional) - this is fine
                str(self.q_para.id): 'para'
            })
