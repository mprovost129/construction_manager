from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('subscriptions', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationsubscription',
            name='plan_key',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='subscriptioncheckoutattempt',
            name='plan_key',
            field=models.CharField(default='standard_monthly', max_length=40),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='subscriptioncheckoutattempt',
            name='stripe_price_id',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
    ]
