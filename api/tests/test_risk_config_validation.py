"""Startup validation of the risk-scoring thresholds (issue #67, review F2).

``RISK_CHALLENGE_THRESHOLD`` and ``RISK_TERMINATE_THRESHOLD`` are each
independently valid, so without a model-level validator a deployment could
set challenge ABOVE terminate — and a soft-only score at/above terminate
but below challenge would match neither branch of the risk evaluation and
silently stay ALLOW. Likewise, the documented defence-in-depth invariant
("the largest single-evaluation soft batch sits below the terminate
threshold") was prose, not code: misconfigured weights could void it
without anything failing. Both must refuse to boot instead.
"""

import pytest
from pydantic import ValidationError

from app.lib.config import Settings

pytestmark = pytest.mark.no_db

_BASE = {
    "_env_file": None,
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/example_test",
    "JWT_SECRET_KEY": "unit-test-secret-key-0123456789abcdef",
}


def _settings(**overrides) -> Settings:
    return Settings(**{**_BASE, **overrides})


class TestThresholdOrdering:
    def test_defaults_are_valid(self):
        s = _settings()
        assert s.RISK_CHALLENGE_THRESHOLD < s.RISK_TERMINATE_THRESHOLD

    def test_challenge_above_terminate_is_refused(self):
        with pytest.raises(ValidationError, match="strictly less"):
            _settings(RISK_CHALLENGE_THRESHOLD=120, RISK_TERMINATE_THRESHOLD=100)

    def test_challenge_equal_to_terminate_is_refused(self):
        with pytest.raises(ValidationError, match="strictly less"):
            _settings(RISK_CHALLENGE_THRESHOLD=100, RISK_TERMINATE_THRESHOLD=100)


class TestSoftBatchBackstop:
    def test_session_start_soft_sum_at_terminate_is_refused(self):
        # 40 + 40 + 10 + 30 = 120 >= 100: a single first-request batch of
        # soft evidence could cross the terminate line — refused at boot.
        with pytest.raises(ValidationError, match="soft-signal sum"):
            _settings(RISK_SCORE_NEW_DEVICE=40, RISK_SCORE_NEW_COUNTRY=40)

    def test_mid_session_soft_sum_at_terminate_is_refused(self):
        with pytest.raises(ValidationError, match="mid-session soft-signal sum"):
            _settings(RISK_SCORE_DEVICE_CHANGE=80, RISK_SCORE_VOLUME_ANOMALY=30)

    def test_soft_sum_below_terminate_is_accepted(self):
        s = _settings(RISK_TERMINATE_THRESHOLD=200, RISK_SCORE_NEW_DEVICE=40)
        assert s.RISK_TERMINATE_THRESHOLD == 200
