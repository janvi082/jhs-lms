from django import forms
from content.models import Subject, Topic, Video, Resource, SiteConfig
from quizzes.models import Question, Choice
from accounts.models import User

class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ['name', 'description', 'is_active', 'passing_score_override']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Communication Skills'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Subject overview...'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'passing_score_override': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to use default (75%)'}),
        }
        labels = {
            'is_active': 'Active in Portal Catalog',
            'passing_score_override': 'Passing Score Override (%)',
        }

class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ['subject', 'name', 'summary', 'status', 'passing_score_override', 'assessment_required']
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Email Etiquette'}),
            'summary': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '2-4 line short summary explaining what the learner will learn...'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'passing_score_override': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to use default (75%)'}),
            'assessment_required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'status': 'Publication Status',
            'passing_score_override': 'Passing Score Override (%)',
            'assessment_required': 'Assessment Required',
        }
class TopicAddForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ['subject', 'name', 'summary', 'passing_score_override', 'assessment_required']
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Email Etiquette'}),
            'summary': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '2-4 line short summary...'}),
            'passing_score_override': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to use default (75%)'}),
            'assessment_required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'passing_score_override': 'Passing Score Override (%)',
            'assessment_required': 'Assessment Required',
        }


class VideoForm(forms.ModelForm):
    class Meta:
        model = Video
        fields = ['title', 'url', 'duration', 'description']
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
    class Meta:
        model = Resource
        fields = ['title', 'resource_type', 'url', 'description']
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

class LearnerEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

# New form for admin password reset
class LearnerPasswordResetForm(forms.Form):
    new_password = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        strip=False,
    )
    confirm_password = forms.CharField(
        label='Confirm New Password',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        strip=False,
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get('new_password')
        pw2 = cleaned.get('confirm_password')
        if pw1 and pw2 and pw1 != pw2:
            raise forms.ValidationError('Passwords do not match.')
        from django.contrib.auth.password_validation import validate_password
        if pw1:
            try:
                validate_password(pw1, user=self.user)
            except forms.ValidationError as e:
                raise forms.ValidationError(e.messages)
        return cleaned

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

# New form for Admin Access UI
class LearnerAccessForm(forms.Form):
    access_data = forms.CharField(widget=forms.HiddenInput)

    def clean_access_data(self):
        import json
        data_str = self.cleaned_data['access_data']
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            raise forms.ValidationError('Invalid JSON data.')
        if not isinstance(data, list):
            raise forms.ValidationError('Access data must be a list.')
        valid_types = {'subject', 'topic', 'video', 'resource'}
        cleaned = []
        from content.models import Subject, Topic, Video, Resource
        model_map = {
            'subject': Subject,
            'topic': Topic,
            'video': Video,
            'resource': Resource,
        }
        for entry in data:
            if not isinstance(entry, dict):
                raise forms.ValidationError('Each entry must be an object.')
            typ = entry.get('type')
            obj_id = entry.get('id')
            if typ not in valid_types:
                raise forms.ValidationError(f'Invalid type: {typ}')
            if not isinstance(obj_id, int):
                raise forms.ValidationError('ID must be an integer.')
            Model = model_map[typ]
            try:
                obj = Model.objects.get(pk=obj_id)
            except Model.DoesNotExist:
                raise forms.ValidationError(f'{typ.title()} with id {obj_id} does not exist.')
            cleaned.append({'type': typ, 'id': obj_id})
        return cleaned
