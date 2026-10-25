from django.db import models


class Entry(models.Model):
    sequence = models.BigAutoField(primary_key=True)
    uuid = models.CharField(max_length=36, unique=True)
    batch_id = models.CharField(max_length=36, db_index=True)
    family_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    should_display_on_index = models.BooleanField(default=True)
    type = models.CharField(max_length=20)
    content = models.TextField()
    created_at = models.DateTimeField(null=True, db_index=True)

    class Meta:
        db_table = "tracefox_entries"
        indexes = [models.Index(fields=["type", "should_display_on_index"], name="tracefox_type_display_idx")]
        verbose_name_plural = "entries"


class EntryTag(models.Model):
    entry_uuid = models.CharField(max_length=36)
    tag = models.CharField(max_length=191, db_index=True)

    class Meta:
        db_table = "tracefox_entries_tags"
        constraints = [models.UniqueConstraint(fields=["entry_uuid", "tag"], name="tracefox_entry_tag_unique")]


class MonitoredTag(models.Model):
    tag = models.CharField(max_length=191, primary_key=True)

    class Meta:
        db_table = "tracefox_monitoring"


class Setting(models.Model):
    key = models.CharField(max_length=191, primary_key=True)
    value = models.TextField(null=True)

    class Meta:
        db_table = "tracefox_settings"
