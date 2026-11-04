from django.db import migrations

def seed(apps, schema_editor):
    apps.get_model("attendance", "WeeklyHoliday").objects.get_or_create(weekday=6)

class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
