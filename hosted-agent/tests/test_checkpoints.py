import importlib
import os

os.environ.setdefault(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://example.services.ai.azure.com/api/projects/test",
)

from travel_agent.checkpoints import _ALLOWED_CHECKPOINT_TYPES


def test_checkpoint_allowlist_contains_only_importable_workflow_types():
    assert (
        "travel_agent.models:SubmissionApprovalRequest"
        in _ALLOWED_CHECKPOINT_TYPES
    )

    for type_name in _ALLOWED_CHECKPOINT_TYPES:
        module_name, qualname = type_name.split(":", maxsplit=1)
        value = importlib.import_module(module_name)
        for attribute in qualname.split("."):
            value = getattr(value, attribute)
        assert isinstance(value, type)
