# Generated for Historical Timing Provenance

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('progress', '0003_topicprogress_frozen_deadline'),
    ]

    operations = [
        migrations.AddField(
            model_name='topicprogress',
            name='timing_provenance',
            field=models.CharField(
                blank=True,
                choices=[
                    ('system_captured', 'System Captured'),
                    ('retrospectively_reconstructed', 'Retrospectively Reconstructed'),
                ],
                help_text='Provenance of the frozen deadline benchmark.',
                max_length=32,
                null=True,
            ),
        ),
    ]
