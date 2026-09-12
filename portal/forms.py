from django import forms
from content.models import Subject, Topic, Video, Resource, SiteConfig
from quizzes.models import Question, Choice
from accounts.models import User

class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ['name', 'description', 'order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Communication Skills'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Subject overview...'}),
            'order': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'is_active': 'Active in Portal Catalog'
        }

class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ['subject', 'name', 'summary', 'status', 'order', 'passing_score_override']
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Email Etiquette'}),
            'summary': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '2-4 line short summary explaining what the learner will learn...'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'order': forms.NumberInput(attrs={'class': 'form-control'}),
            'passing_score_override': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to use default (75%)'}),
        }
        labels = {
            'status': 'Publication Status',
            'passing_score_override': 'Passing Score Override (%)'
        }
class TopicAddForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ['subject', 'name', 'summary', 'order', 'passing_score_override']
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Email Etiquette'}),
            'summary': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '2-4 line short summary...'}),
            'order': forms.NumberInput(attrs={'class': 'form-control'}),
            'passing_score_override': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to use default (75%)'}),
        }
        labels = {
            'passing_score_override': 'Passing Score Override (%)',
        }


class VideoForm(forms.ModelForm):
    order = forms.IntegerField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Optional order'}))

    class Meta:
        model = Video
        fields = ['title', 'url', 'duration', 'description', 'order']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Video Title'}),
            'url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://onedrive.live.com/... or video link'}),
            'duration': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 18:42'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional video notes...'}),
        }

    def clean_url(self):
        url = self.cleaned_data.get('url', '').strip()
        if url.startswith('http://'):
            url = 'https://' + url[7:]
        elif url and not url.startswith('https://'):
            url = 'https://' + url
        return url

class ResourceForm(forms.ModelForm):
    order = forms.IntegerField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Optional order'}))

    class Meta:
        model = Resource
        fields = ['title', 'resource_type', 'url', 'description', 'order']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Material Title'}),
            'resource_type': forms.Select(attrs={'class': 'form-select'}),
            'url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://onedrive.live.com/... or file link'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional description...'}),
        }

    def clean_url(self):
        url = self.cleaned_data.get('url', '').strip()
        if url.startswith('http://'):
            url = 'https://' + url[7:]
        elif url and not url.startswith('https://'):
            url = 'https://' + url
        return url

class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ['text', 'question_type', 'required', 'accepted_answers', 'order']
        widgets = {
            'text': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Enter question text...'}),
            'question_type': forms.Select(attrs={'class': 'form-select'}),
            'required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'accepted_answers': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Enter accepted answers, one per line...'}),
            'order': forms.NumberInput(attrs={'class': 'form-control'}),
        }

class LearnerForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Initial Password'}), required=False)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'is_active': 'Account Active'
        }

class SiteConfigForm(forms.ModelForm):
    class Meta:
        model = SiteConfig
        fields = ['default_passing_score', 'default_required_question_count']
        widgets = {
            'default_passing_score': forms.NumberInput(attrs={'class': 'form-control'}),
            'default_required_question_count': forms.NumberInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'default_passing_score': 'Default Passing Score (%)',
            'default_required_question_count': 'Minimum Questions Required to Publish a Topic'
        }
