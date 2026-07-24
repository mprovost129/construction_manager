from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0008_alter_activityevent_event_type_schedulemilestone'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='activityevent',
            index=models.Index(
                fields=['project', '-created_at'],
                name='activity_proj_created_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='activityevent',
            index=models.Index(
                fields=['project', 'event_type', '-created_at'],
                name='activity_proj_type_idx',
            ),
        ),
    ]
