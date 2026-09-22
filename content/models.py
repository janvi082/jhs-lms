from django.db import models
from django.utils.text import slugify

class SiteConfig(models.Model):
    default_passing_score = models.PositiveIntegerField(
        default=75,
        help_text="Default passing score percentage across all quizzes (0-100)"
    )
    default_required_question_count = models.PositiveIntegerField(
        default=5,
        help_text="Default minimum questions required for a topic quiz to be marked assessment-ready"
    )

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(id=1)
        return obj

    def __str__(self):
        return f"Site Configuration (Default Pass: {self.default_passing_score}%, Required Questions: {self.default_required_question_count})"

class Subject(models.Model):
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0, blank=True)
    is_active = models.BooleanField(default=True)
    passing_score_override = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional passing score percentage override for all topics in this subject"
    )

    class Meta:
        ordering = ['order', 'name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if self.pk is None and not self.order:
            from django.db.models import Max
            max_order = Subject.objects.aggregate(Max('order'))['order__max']
            self.order = (max_order or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def active_topics_count(self):
        return self.topics.filter(status='published').count()

class Topic(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_ARCHIVED = 'archived'

    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_ARCHIVED, 'Archived'),
    ]

    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='topics')
    name = models.CharField(max_length=200)
    summary = models.CharField(max_length=400, help_text="Short summary explaining what the learner will learn (2-4 lines max)")
    order = models.PositiveIntegerField(default=0, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    is_active = models.BooleanField(default=True)
    passing_score_override = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional passing score percentage override for this specific topic"
    )
    assessment_required = models.BooleanField(
        default=True,
        help_text="Whether learners must complete an assessment to complete this topic."
    )

    class Meta:
        ordering = ['order', 'name']

    def save(self, *args, **kwargs):
        self.is_active = (self.status == self.STATUS_PUBLISHED)
        if self.pk is None and not self.order:
            from django.db.models import Max
            max_order = Topic.objects.filter(subject=self.subject).aggregate(Max('order'))['order__max']
            self.order = (max_order or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject.name} - {self.name}"

    @property
    def effective_passing_score(self):
        if self.passing_score_override is not None:
            return self.passing_score_override
        if self.subject.passing_score_override is not None:
            return self.subject.passing_score_override
        return SiteConfig.get_solo().default_passing_score

    def is_assessment_ready(self):
        required = SiteConfig.get_solo().default_required_question_count
        if required == 0:
            return self.questions.exists()
        return self.questions.count() >= required

class Video(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='videos')
    title = models.CharField(max_length=200)
    url = models.URLField(help_text="Video URL (e.g. OneDrive share link or direct video link)")
    description = models.TextField(blank=True)
    duration = models.CharField(max_length=50, blank=True, help_text="Optional duration string (e.g. 18:42)")
    order = models.PositiveIntegerField(default=0, blank=True)

    class Meta:
        ordering = ['order', 'id']

    def save(self, *args, **kwargs):
        if self.pk is None and not self.order:
            from django.db.models import Max
            max_order = Video.objects.filter(topic=self.topic).aggregate(Max('order'))['order__max']
            self.order = (max_order or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

class Resource(models.Model):
    RESOURCE_TYPE_CHOICES = [
        ('video', 'Video'),
        ('pdf', 'PDF Document'),
        ('ppt', 'PowerPoint Presentation'),
        ('excel', 'Excel Practice File'),
        ('word', 'Word Document'),
        ('image', 'Image'),
        ('practice_file', 'Practice File'),
        ('external_link', 'External Link'),
        ('onedrive', 'OneDrive Link'),
        ('other', 'Other'),
    ]

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='resources')
    title = models.CharField(max_length=200)
    resource_type = models.CharField(max_length=50, choices=RESOURCE_TYPE_CHOICES, default='pdf')
    url = models.URLField(help_text="Resource URL (e.g. OneDrive share link, PDF link, etc.)")
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0, blank=True)

    class Meta:
        ordering = ['order', 'id']

    def save(self, *args, **kwargs):
        if self.pk is None and not self.order:
            max_order = Resource.objects.filter(topic=self.topic).aggregate(models.Max('order'))['order__max']
            self.order = (max_order or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} ({self.get_resource_type_display()})"
