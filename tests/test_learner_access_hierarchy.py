from django.test import TestCase
from portal.forms import LearnerAccessForm
from content.models import Subject, Topic, Video, Resource
import json

class LearnerAccessHierarchyTest(TestCase):
    def setUp(self):
        # Create subjects
        self.subj_a = Subject.objects.create(name='Subject A')
        self.subj_b = Subject.objects.create(name='Subject B')
        # Topics under subjects (summary required)
        self.topic_a1 = Topic.objects.create(name='Topic A1', subject=self.subj_a, summary='summary', status=Topic.STATUS_PUBLISHED)
        self.topic_b1 = Topic.objects.create(name='Topic B1', subject=self.subj_b, summary='summary', status=Topic.STATUS_PUBLISHED)
        # Videos under topics (title and url required)
        self.video_a1 = Video.objects.create(title='Video A1', topic=self.topic_a1, url='http://example.com/video_a1')
        self.video_b1 = Video.objects.create(title='Video B1', topic=self.topic_b1, url='http://example.com/video_b1')
        # Resources under topics (title and url required)
        self.res_a1 = Resource.objects.create(title='Resource A1', topic=self.topic_a1, url='http://example.com/res_a1')
        self.res_b1 = Resource.objects.create(title='Resource B1', topic=self.topic_b1, url='http://example.com/res_b1')

    def _form(self, data):
        return LearnerAccessForm(data={'access_data': json.dumps(data)})

    # ------------------- VALID CASES -------------------
    def test_valid_subject_only(self):
        form = self._form([{'type': 'subject', 'id': self.subj_a.id}])
        self.assertTrue(form.is_valid())

    def test_valid_topic_only_without_subject(self):
        form = self._form([{'type': 'topic', 'id': self.topic_a1.id}])
        self.assertTrue(form.is_valid())

    def test_valid_video_only_without_topic(self):
        form = self._form([{'type': 'video', 'id': self.video_a1.id}])
        self.assertTrue(form.is_valid())

    def test_valid_resource_only_without_topic(self):
        form = self._form([{'type': 'resource', 'id': self.res_a1.id}])
        self.assertTrue(form.is_valid())

    def test_valid_subject_and_matching_topic(self):
        form = self._form([
            {'type': 'subject', 'id': self.subj_a.id},
            {'type': 'topic', 'id': self.topic_a1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_topic_and_matching_video(self):
        form = self._form([
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'video', 'id': self.video_a1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_topic_and_matching_resource(self):
        form = self._form([
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'resource', 'id': self.res_a1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_multiple_matching_levels(self):
        form = self._form([
            {'type': 'subject', 'id': self.subj_a.id},
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'video', 'id': self.video_a1.id},
            {'type': 'resource', 'id': self.res_a1.id},
        ])
        self.assertTrue(form.is_valid())

    # ------------------- VALID INDEPENDENT HIERARCHY CASES -------------------
    def test_valid_independent_subject_and_topic(self):
        form = self._form([
            {'type': 'subject', 'id': self.subj_a.id},
            {'type': 'topic', 'id': self.topic_b1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_independent_topic_and_video(self):
        form = self._form([
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'video', 'id': self.video_b1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_independent_topic_and_resource(self):
        form = self._form([
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'resource', 'id': self.res_b1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_mixed_subject_with_matching_and_unrelated_topic(self):
        form = self._form([
            {'type': 'subject', 'id': self.subj_a.id},
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'topic', 'id': self.topic_b1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_mixed_topic_with_matching_and_unrelated_video(self):
        form = self._form([
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'video', 'id': self.video_a1.id},
            {'type': 'video', 'id': self.video_b1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_mixed_topic_with_matching_and_unrelated_resource(self):
        form = self._form([
            {'type': 'topic', 'id': self.topic_a1.id},
            {'type': 'resource', 'id': self.res_a1.id},
            {'type': 'resource', 'id': self.res_b1.id},
        ])
        self.assertTrue(form.is_valid())

    def test_valid_cross_hierarchy_subject_topic_video(self):
        form = self._form([
            {'type': 'subject', 'id': self.subj_a.id},
            {'type': 'topic', 'id': self.topic_b1.id},
            {'type': 'video', 'id': self.video_a1.id},
        ])
        self.assertTrue(form.is_valid())

    # ------------------- NONEXISTENT ID CASES -------------------
    def test_invalid_nonexistent_subject(self):
        form = self._form([{'type': 'subject', 'id': 999999}])
        self.assertFalse(form.is_valid())
        self.assertIn('Subject with id 999999 does not exist', str(form.errors))

    def test_invalid_nonexistent_topic(self):
        form = self._form([{'type': 'topic', 'id': 999999}])
        self.assertFalse(form.is_valid())
        self.assertIn('Topic with id 999999 does not exist', str(form.errors))

    def test_invalid_nonexistent_video(self):
        form = self._form([{'type': 'video', 'id': 999999}])
        self.assertFalse(form.is_valid())
        self.assertIn('Video with id 999999 does not exist', str(form.errors))

    def test_invalid_nonexistent_resource(self):
        form = self._form([{'type': 'resource', 'id': 999999}])
        self.assertFalse(form.is_valid())
        self.assertIn('Resource with id 999999 does not exist', str(form.errors))
