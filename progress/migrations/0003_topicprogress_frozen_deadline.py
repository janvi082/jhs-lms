# Generated manually for Foundation Performance Monitoring

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('progress', '0002_topicprogress_expected_end_day_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='topicprogress',
            name='frozen_deadline',
            field=models.DateField(
                blank=True,
                help_text='Benchmark nominal deadline frozen when topic completion is first recorded.',
                null=True
            ),
        ),
    ]
