from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_ADMIN = 'admin'
    ROLE_LEARNER = 'learner'
    
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_LEARNER, 'Learner'),
    ]
    
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_LEARNER,
        help_text="Role determining application access (Admin or Learner)"
    )

    @property
    def is_admin_user(self):
        return self.role == self.ROLE_ADMIN or self.is_staff or self.is_superuser

    @property
    def is_learner(self):
        return self.role == self.ROLE_LEARNER

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
