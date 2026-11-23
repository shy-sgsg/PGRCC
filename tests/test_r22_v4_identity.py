from __future__ import annotations

import pytest

from scripts.phase_r.r22_v4_identity import branch_identity, branch_identity_key


def test_v4_branch_identity_separates_modes_and_normalizes_not_applicable():
    base = {
        "case_id": "case-1", "method_id": "C4", "condition": "C4_Mode",
        "role": "ON", "branch_name": "C4_modea_ON",
        "source_configuration_identity": {"method_config_sha256": "a" * 64},
    }
    mode_a = branch_identity({**base, "mode": "Mode-A"})
    mode_b = branch_identity({**base, "mode": "Mode-B"})
    implicit = branch_identity({**base, "mode": None})
    explicit = branch_identity({**base, "mode": "not_applicable"})

    assert branch_identity_key(mode_a) != branch_identity_key(mode_b)
    changed_condition = branch_identity({**base, "mode": "Mode-A", "condition": "C4_OtherCondition"})
    assert branch_identity_key(mode_a) != branch_identity_key(changed_condition)
    assert branch_identity_key(implicit) == branch_identity_key(explicit)
    assert mode_a["condition"] == "C4_Mode"
    assert mode_a["source_configuration_identity"] == base["source_configuration_identity"]


def test_v4_branch_identity_does_not_substitute_condition_for_method_id():
    record = {
        "case_id": "case-1", "condition": "C4_Mode-A", "role": "ON",
        "branch_name": "C4_modea_ON", "mode": "Mode-A",
        "source_configuration_identity": {"source": "fixture"},
    }
    with pytest.raises(ValueError, match="distinct explicit method_id, condition"):
        branch_identity(record)
