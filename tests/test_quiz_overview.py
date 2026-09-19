from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic, SiteConfig

class QuizOverviewFilterTests(TestCase):
    def setUp(self):
        # Site config
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        # Users
        self.admin = User.objects.create_user(
            username='admin', password='adminpass', role=User.ROLE_ADMIN, is_staff=True
        )
        self.client = Client()
        self.client.login(username='admin', password='adminpass')

        # Subjects
        self.subject1 = Subject.objects.create(name='Subject 1', slug='subj-1', order=1)
        self.subject2 = Subject.objects.create(name='Subject 2', slug='subj-2', order=2)

        # Topics
        self.topic1_1 = Topic.objects.create(subject=self.subject1, name='Topic 1.1', order=1)
        self.topic1_2 = Topic.objects.create(subject=self.subject1, name='Topic 1.2', order=2)
        self.topic2_1 = Topic.objects.create(subject=self.subject2, name='Topic 2.1', order=1)

        self.url = reverse('portal_quizzes_overview')

    def test_no_filters_shows_all_topics(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        
        quiz_data = response.context['quiz_data']
        self.assertEqual(len(quiz_data), 3)
        topics = [item['topic'] for item in quiz_data]
        self.assertIn(self.topic1_1, topics)
        self.assertIn(self.topic1_2, topics)
        self.assertIn(self.topic2_1, topics)

    def test_subject_filter_shows_only_subject_topics(self):
        response = self.client.get(self.url, {'subject_id': self.subject1.id})
        self.assertEqual(response.status_code, 200)
        
        quiz_data = response.context['quiz_data']
        self.assertEqual(len(quiz_data), 2)
        topics = [item['topic'] for item in quiz_data]
        self.assertIn(self.topic1_1, topics)
        self.assertIn(self.topic1_2, topics)
        self.assertNotIn(self.topic2_1, topics)
        
        self.assertEqual(response.context['selected_subject'], self.subject1)

    def test_subject_and_topic_filter_shows_selected_topic(self):
        response = self.client.get(self.url, {'subject_id': self.subject1.id, 'topic_id': self.topic1_1.id})
        self.assertEqual(response.status_code, 200)
        
        quiz_data = response.context['quiz_data']
        self.assertEqual(len(quiz_data), 1)
        self.assertEqual(quiz_data[0]['topic'], self.topic1_1)
        
        self.assertEqual(response.context['selected_subject'], self.subject1)
        self.assertEqual(response.context['selected_topic'], self.topic1_1)

    def test_topic_without_subject_derives_subject(self):
        response = self.client.get(self.url, {'topic_id': self.topic2_1.id})
        self.assertEqual(response.status_code, 200)
        
        quiz_data = response.context['quiz_data']
        self.assertEqual(len(quiz_data), 1)
        self.assertEqual(quiz_data[0]['topic'], self.topic2_1)
        
        # Subject should be derived from the topic
        self.assertEqual(response.context['selected_subject'], self.subject2)
        self.assertEqual(response.context['selected_topic'], self.topic2_1)

    def test_mismatched_subject_and_topic_discards_topic(self):
        # Pass Subject 1 but Topic 2.1 (which belongs to Subject 2)
        response = self.client.get(self.url, {'subject_id': self.subject1.id, 'topic_id': self.topic2_1.id})
        self.assertEqual(response.status_code, 200)
        
        # Topic should be discarded, but Subject 1 remains
        self.assertEqual(response.context['selected_subject'], self.subject1)
        self.assertIsNone(response.context['selected_topic'])
        
        quiz_data = response.context['quiz_data']
        self.assertEqual(len(quiz_data), 2)  # All topics for Subject 1

    def test_invalid_subject_id_ignored(self):
        response = self.client.get(self.url, {'subject_id': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected_subject'])
        self.assertEqual(len(response.context['quiz_data']), 3)

    def test_invalid_topic_id_ignored(self):
        response = self.client.get(self.url, {'subject_id': self.subject1.id, 'topic_id': '9999'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_subject'], self.subject1)
        self.assertIsNone(response.context['selected_topic'])
        self.assertEqual(len(response.context['quiz_data']), 2)

    def test_dependent_topic_queryset(self):
        response = self.client.get(self.url, {'subject_id': self.subject1.id})
        self.assertEqual(response.status_code, 200)
        
        topics_for_dropdown = response.context['topics']
        self.assertEqual(len(topics_for_dropdown), 2)
        self.assertIn(self.topic1_1, topics_for_dropdown)
        self.assertIn(self.topic1_2, topics_for_dropdown)
        self.assertNotIn(self.topic2_1, topics_for_dropdown)

    def test_existing_quiz_metadata_remains_correct(self):
        from quizzes.models import Question
        # Add questions to Topic 1.1 to verify count and readiness
        Question.objects.create(topic=self.topic1_1, text="Q1", order=1)
        Question.objects.create(topic=self.topic1_1, text="Q2", order=2)
        
        response = self.client.get(self.url, {'subject_id': self.subject1.id, 'topic_id': self.topic1_1.id})
        quiz_data = response.context['quiz_data']
        self.assertEqual(len(quiz_data), 1)
        
        item = quiz_data[0]
        self.assertEqual(item['question_count'], 2)
        # Needs 5 questions, has 2, so should be False
        self.assertFalse(item['is_ready'])
        
        # Add 3 more questions
        for i in range(3):
            Question.objects.create(topic=self.topic1_1, text=f"Q_extra_{i}", order=3+i)
            
        response = self.client.get(self.url, {'subject_id': self.subject1.id, 'topic_id': self.topic1_1.id})
        quiz_data = response.context['quiz_data']
        item = quiz_data[0]
        self.assertEqual(item['question_count'], 5)
        self.assertTrue(item['is_ready'])
