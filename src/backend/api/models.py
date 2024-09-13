import re
from uuid import uuid4, UUID

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.validators import MinLengthValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_celery_results.models import TaskResult
from uuid6 import uuid7

from api.db_utils import ProviderEnumField, StateEnumField, ScanTriggerEnumField
from api.exceptions import ModelValidationError
from api.rls import RowLevelSecurityConstraint
from api.rls import RowLevelSecurityProtectedModel


class StateChoices(models.TextChoices):
    AVAILABLE = "available", _("Available")
    SCHEDULED = "scheduled", _("Scheduled")
    EXECUTING = "executing", _("Executing")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class Provider(RowLevelSecurityProtectedModel):
    class ProviderChoices(models.TextChoices):
        AWS = "aws", _("AWS")
        AZURE = "azure", _("Azure")
        GCP = "gcp", _("GCP")
        KUBERNETES = "kubernetes", _("Kubernetes")

    @staticmethod
    def validate_aws_uid(value):
        if not re.match(r"^\d{12}$", value):
            raise ModelValidationError(
                detail="AWS provider ID must be exactly 12 digits.",
                code="aws-uid",
                pointer="/data/attributes/uid",
            )

    @staticmethod
    def validate_azure_uid(value):
        try:
            val = UUID(value, version=4)
            if str(val) != value:
                raise ValueError
        except ValueError:
            raise ModelValidationError(
                detail="Azure provider ID must be a valid UUID.",
                code="azure-uid",
                pointer="/data/attributes/uid",
            )

    @staticmethod
    def validate_gcp_uid(value):
        if not re.match(r"^[a-z][a-z0-9-]{5,29}$", value):
            raise ModelValidationError(
                detail="GCP provider ID must be 6 to 30 characters, start with a letter, and contain only lowercase "
                "letters, numbers, and hyphens.",
                code="gcp-uid",
                pointer="/data/attributes/uid",
            )

    @staticmethod
    def validate_kubernetes_uid(value):
        if not re.match(r"^[a-z0-9]([-a-z0-9]{1,61}[a-z0-9])?$", value):
            raise ModelValidationError(
                detail="K8s provider ID must be up to 63 characters, start and end with a lowercase letter or number, "
                "and contain only lowercase alphanumeric characters and hyphens.",
                code="kubernetes-uid",
                pointer="/data/attributes/uid",
            )

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    inserted_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)
    provider = ProviderEnumField(
        choices=ProviderChoices.choices, default=ProviderChoices.AWS
    )
    uid = models.CharField(
        "Unique identifier for the provider, set by the provider",
        max_length=63,
        blank=False,
        validators=[MinLengthValidator(3)],
    )
    alias = models.CharField(
        blank=True, null=True, max_length=100, validators=[MinLengthValidator(3)]
    )
    connected = models.BooleanField(null=True, blank=True)
    connection_last_checked_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    scanner_args = models.JSONField(default=dict, blank=True)

    def clean(self):
        super().clean()
        getattr(self, f"validate_{self.provider}_uid")(self.uid)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta(RowLevelSecurityProtectedModel.Meta):
        db_table = "providers"

        constraints = [
            models.UniqueConstraint(
                fields=("tenant_id", "provider", "uid"),
                name="unique_provider_uids",
            ),
            RowLevelSecurityConstraint(
                field="tenant_id",
                name="rls_on_%(class)s",
                statements=["SELECT", "INSERT", "UPDATE", "DELETE"],
            ),
        ]


class Scan(RowLevelSecurityProtectedModel):
    class TriggerChoices(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        MANUAL = "manual", _("Manual")

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    name = models.CharField(
        blank=True, null=True, max_length=100, validators=[MinLengthValidator(3)]
    )
    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="scans",
        related_query_name="scan",
    )
    trigger = ScanTriggerEnumField(
        choices=TriggerChoices.choices,
    )
    state = StateEnumField(choices=StateChoices.choices, default=StateChoices.AVAILABLE)
    unique_resource_count = models.IntegerField(default=0)
    progress = models.IntegerField(default=0)
    scanner_args = models.JSONField(default=dict)
    duration = models.IntegerField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    inserted_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # TODO: task foreign key
    # TODO: mutelist foreign key

    class Meta(RowLevelSecurityProtectedModel.Meta):
        db_table = "scans"

        constraints = [
            RowLevelSecurityConstraint(
                field="tenant_id",
                name="rls_on_%(class)s",
                statements=["SELECT", "INSERT", "UPDATE", "DELETE"],
            ),
        ]

        indexes = [
            models.Index(
                fields=["provider", "state", "trigger", "scheduled_at"],
                name="scans_prov_state_trig_sche_idx",
            ),
        ]


class Task(RowLevelSecurityProtectedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    inserted_at = models.DateTimeField(auto_now_add=True, editable=False)
    task_runner_task = models.OneToOneField(
        TaskResult,
        on_delete=models.CASCADE,
        related_name="task",
        related_query_name="task",
        null=True,
        blank=True,
    )

    class Meta(RowLevelSecurityProtectedModel.Meta):
        db_table = "tasks"

        constraints = [
            RowLevelSecurityConstraint(
                field="tenant_id",
                name="rls_on_%(class)s",
                statements=["SELECT", "INSERT", "UPDATE", "DELETE"],
            ),
        ]

        indexes = [
            models.Index(
                fields=["id", "task_runner_task"],
                name="tasks_id_trt_id_idx",
            ),
        ]


class ResourceTag(RowLevelSecurityProtectedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    inserted_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    key = models.TextField(blank=False)
    value = models.TextField(blank=False)

    text_search = models.GeneratedField(
        expression=SearchVector("key", weight="A", config="simple")
        + SearchVector("value", weight="B", config="simple"),
        output_field=SearchVectorField(),
        db_persist=True,
        null=True,
        editable=False,
    )

    class Meta(RowLevelSecurityProtectedModel.Meta):
        db_table = "resource_tags"

        indexes = [
            GinIndex(fields=["text_search"], name="gin_resource_tags_search_idx"),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=("tenant_id", "key", "value"),
                name="unique_resource_tags_by_tenant_key_value",
            ),
            RowLevelSecurityConstraint(
                field="tenant_id",
                name="rls_on_%(class)s",
                statements=["SELECT"],
            ),
        ]


class Resource(RowLevelSecurityProtectedModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    inserted_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="resources",
        related_query_name="resource",
    )

    uid = models.TextField(
        "Unique identifier for the resource, set by the provider", blank=False
    )
    name = models.TextField("Name of the resource, as set in the provider", blank=False)
    region = models.TextField(
        "Location of the resource, as set by the provider", blank=False
    )
    service = models.TextField(
        "Service of the resource, as set by the provider", blank=False
    )
    type = models.TextField("Type of the resource, as set by the provider", blank=False)

    text_search = models.GeneratedField(
        expression=SearchVector("uid", weight="A", config="simple")
        + SearchVector("name", weight="B", config="simple")
        + SearchVector("region", weight="C", config="simple")
        + SearchVector("service", "type", weight="D", config="simple"),
        output_field=SearchVectorField(),
        db_persist=True,
        null=True,
        editable=False,
    )

    tags = models.ManyToManyField(
        ResourceTag,
        verbose_name="Tags associated with the resource, by provider",
        through="ResourceTagMapping",
    )

    def get_tags(self) -> dict:
        return {tag.key: tag.value for tag in self.tags.all()}

    def clear_tags(self):
        self.tags.clear()
        self.save()

    def upsert_or_delete_tags(self, tags: list[ResourceTag] | None):
        if tags is None:
            self.clear_tags()
            return

        # Add new relationships with the tenant_id field
        for tag in tags:
            ResourceTagMapping.objects.update_or_create(
                tag=tag, resource=self, tenant_id=self.tenant_id
            )

        # Save the instance
        self.save()

    class Meta(RowLevelSecurityProtectedModel.Meta):
        db_table = "resources"

        indexes = [
            models.Index(
                fields=["uid", "region", "service", "name"],
                name="idx_resource_uid_reg_serv_name",
            ),
            GinIndex(fields=["text_search"], name="gin_resources_search_idx"),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=("tenant_id", "provider_id", "uid"),
                name="unique_resources_by_provider",
            ),
            RowLevelSecurityConstraint(
                field="tenant_id",
                name="rls_on_%(class)s",
                statements=["SELECT"],
            ),
        ]


class ResourceTagMapping(RowLevelSecurityProtectedModel):
    # NOTE that we don't really need a primary key here,
    #      but everything is easier with django if we do
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    resource = models.ForeignKey(Resource, on_delete=models.DO_NOTHING)
    tag = models.ForeignKey(ResourceTag, on_delete=models.CASCADE)

    class Meta(RowLevelSecurityProtectedModel.Meta):
        db_table = "resource_tag_mappings"

        # django will automatically create indexes for:
        #   - resource_id
        #   - tag_id
        #   - tenant_id
        #   - id

        constraints = [
            models.UniqueConstraint(
                fields=("tenant_id", "resource_id", "tag_id"),
                name="unique_resource_tag_mappings_by_tenant_resource_tag",
            ),
            RowLevelSecurityConstraint(
                field="tenant_id",
                name="rls_on_%(class)s",
                statements=["SELECT"],
            ),
        ]
