import json
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.services import submit_quiz_attempt

class QuestionSequencingTests(TestCase):
    def setUp(self):
        # Set up site config
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        # Users
        self.admin = User.objects.create_user(username='admin', password='password', role=User.ROLE_ADMIN, is_staff=True)
        self.learner = User.objects.create_user(username='learner', password='password', role=User.ROLE_LEARNER)
        
        self.client = Client()
        
        # Subject & Topic
        self.subject = Subject.objects.create(name='Subject', slug='subject', order=1)
        self.topic = Topic.objects.create(subject=self.subject, name='Topic', order=1)
        
        self.reorder_url = reverse('portal_questions_reorder')

    def test_model_append_order(self):
        # 1. First Question gets order 1
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        self.assertEqual(q1.order, 1)
        
        # 2. Second Question gets MAX + 1
        q2 = Question.objects.create(topic=self.topic, text="Q2")
        self.assertEqual(q2.order, 2)
        
        # 3. Delete a question, leaving a gap, then create another question
        q2.delete()
        q3 = Question.objects.create(topic=self.topic, text="Q3")
        self.assertEqual(q3.order, 2) # max is 1, so 1+1=2. Let's create more to make a real gap.
        
        q4 = Question.objects.create(topic=self.topic, text="Q4")
        self.assertEqual(q4.order, 3)
        
        # Now we have Q1 (order=1), Q3 (order=2), Q4 (order=3)
        # Delete Q3 (order=2) to leave gap [1, 3]
        q3.delete()
        
        q5 = Question.objects.create(topic=self.topic, text="Q5")
        self.assertEqual(q5.order, 4) # MAX is 3, so MAX+1 is 4, not count()+1=3.
        
        # 4. Existing Question.save() does not automatically change its existing order
        q1.text = "Q1 updated"
        q1.save()
        self.assertEqual(q1.order, 1)

    def test_reorder_valid(self):
        self.client.login(username='admin', password='password')
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        q2 = Question.objects.create(topic=self.topic, text="Q2")
        q3 = Question.objects.create(topic=self.topic, text="Q3")
        
        # Reorder to Q3, Q1, Q2
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': [q3.id, q1.id, q2.id]
        }), content_type='application/json')
        
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        
        q1.refresh_from_db()
        q2.refresh_from_db()
        q3.refresh_from_db()
        self.assertEqual(q3.order, 1)
        self.assertEqual(q1.order, 2)
        self.assertEqual(q2.order, 3)

    def test_reorder_duplicate_ids_rejected(self):
        self.client.login(username='admin', password='password')
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        q2 = Question.objects.create(topic=self.topic, text="Q2")
        
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': [q1.id, q1.id]
        }), content_type='application/json')
        
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])
        self.assertIn('Duplicate', resp.json()['error'])

    def test_reorder_missing_ids_rejected(self):
        self.client.login(username='admin', password='password')
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        q2 = Question.objects.create(topic=self.topic, text="Q2")
        
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': [q1.id]
        }), content_type='application/json')
        
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_reorder_extra_ids_rejected(self):
        self.client.login(username='admin', password='password')
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': [q1.id, 9999]
        }), content_type='application/json')
        
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_reorder_cross_topic_rejected(self):
        self.client.login(username='admin', password='password')
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        
        topic2 = Topic.objects.create(subject=self.subject, name='Topic 2', order=2)
        q2 = Question.objects.create(topic=topic2, text="Q2")
        
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': [q1.id, q2.id]
        }), content_type='application/json')
        
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_reorder_wrong_count_rejected(self):
        self.client.login(username='admin', password='password')
        q1 = Question.objects.create(topic=self.topic, text="Q1")
        
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': []
        }), content_type='application/json')
        
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_reorder_invalid_json_rejected(self):
        self.client.login(username='admin', password='password')
        resp = self.client.post(self.reorder_url, "INVALID JSON", content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_reorder_invalid_topic_rejected(self):
        self.client.login(username='admin', password='password')
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': 9999,
            'question_ids': []
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_reorder_non_post_rejected(self):
        self.client.login(username='admin', password='password')
        resp = self.client.get(self.reorder_url)
        self.assertEqual(resp.status_code, 400)

    def test_reorder_learner_rejected(self):
        self.client.login(username='learner', password='password')
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': []
        }), content_type='application/json')
        # Admin required redirect/forbidden
        self.assertEqual(resp.status_code, 403)

    def test_reorder_unauthenticated_rejected(self):
        resp = self.client.post(self.reorder_url, json.dumps({
            'topic_id': self.topic.id,
            'question_ids': []
        }), content_type='application/json')
        self.assertTrue(resp.status_code in (302, 403))

    def test_historical_regression(self):
        # Existing completed attempt remains in original response order after Question.order is changed
        q1 = Question.objects.create(topic=self.topic, text="Q1", question_type=Question.TYPE_SINGLE_CHOICE)
        c1 = Choice.objects.create(question=q1, text="C1", is_correct=True)
        
        q2 = Question.objects.create(topic=self.topic, text="Q2", question_type=Question.TYPE_SINGLE_CHOICE)
        c2 = Choice.objects.create(question=q2, text="C2", is_correct=True)
        
        q3 = Question.objects.create(topic=self.topic, text="Q3", question_type=Question.TYPE_SINGLE_CHOICE)
        c3 = Choice.objects.create(question=q3, text="C3", is_correct=True)
        
        # Learner submits quiz
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, {
            q1.id: c1.id,
            q2.id: c2.id,
            q3.id: c3.id
        })
        
        # Reorder questions in db
        q3.order = 1
        q3.save()
        q2.order = 2
        q2.save()
        q1.order = 3
        q1.save()
        
        # Check learner result page
        self.client.login(username='learner', password='password')
        result_url = reverse('quiz_result', kwargs={'slug': self.subject.slug, 'topic_id': self.topic.id, 'attempt_id': attempt.id})
        resp = self.client.get(result_url)
        self.assertEqual(resp.status_code, 200)
        
        reviews = resp.context['question_reviews']
        self.assertEqual(len(reviews), 3)
        # Should be original submission order Q1, Q2, Q3
        self.assertEqual(reviews[0]['question_id'], q1.id)
        self.assertEqual(reviews[1]['question_id'], q2.id)
        self.assertEqual(reviews[2]['question_id'], q3.id)
