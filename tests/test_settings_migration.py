"""Tests for the one-time QSettings migration from WorkerBuddy3 -> WorkerBuddy.

v1 main.py wrote to "LynnCove\\WorkerBuddy3"; v1 settings_dialog.py wrote to
"LynnCove\\WorkerBuddy". v2 standardized on "WorkerBuddy" and _migrate_legacy_settings
copies any existing "WorkerBuddy3" data into it without clobbering values the
user has already set. Runs once, marked via a migrated_from_wb3 flag.

These tests use a private "WB_TEST_*" organization name to avoid touching the
user's real Worker Buddy settings on the machine.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QSettings  # noqa: E402


TEST_ORG = "WB_TEST_MIGRATION"
LEGACY_APP = "WorkerBuddy3"
NEW_APP = "WorkerBuddy"


def _clean() -> None:
    """Blow away any state left behind by prior runs."""
    for app in (LEGACY_APP, NEW_APP):
        s = QSettings(TEST_ORG, app)
        s.clear()
        s.sync()


def _migrate(target: QSettings, legacy: QSettings) -> None:
    """The migrator logic, generalized over the org/app pair for testing.

    Mirrors main._migrate_legacy_settings exactly; we can't call that one
    directly because it hardcodes ('LynnCove', 'WorkerBuddy3') and we don't
    want the tests touching a real install.
    """
    if target.value("migrated_from_wb3", False, type=bool):
        return
    if not legacy.allKeys():
        target.setValue("migrated_from_wb3", True)
        return
    for key in legacy.allKeys():
        if not target.contains(key):
            target.setValue(key, legacy.value(key))
    target.setValue("migrated_from_wb3", True)


@pytest.fixture(autouse=True)
def clean_settings():
    """Clear before and after each test so they don't leak into each other."""
    _clean()
    yield
    _clean()


def test_migrates_all_keys_when_target_empty():
    legacy = QSettings(TEST_ORG, LEGACY_APP)
    legacy.setValue("model", "claude-sonnet-4-5-20250929")
    legacy.setValue("always_on_top", True)
    legacy.setValue("opacity", 0.85)
    legacy.setValue("desktop_max_steps", 42)
    legacy.sync()

    target = QSettings(TEST_ORG, NEW_APP)
    assert target.allKeys() == []  # definitely clean

    _migrate(target, legacy)
    target.sync()

    assert target.value("model") == "claude-sonnet-4-5-20250929"
    assert target.value("always_on_top", type=bool) is True
    assert target.value("opacity", type=float) == 0.85
    assert int(target.value("desktop_max_steps")) == 42
    assert target.value("migrated_from_wb3", type=bool) is True


def test_does_not_overwrite_user_overrides():
    legacy = QSettings(TEST_ORG, LEGACY_APP)
    legacy.setValue("model", "claude-sonnet-4-5-20250929")
    legacy.setValue("mode", "browser")
    legacy.sync()

    target = QSettings(TEST_ORG, NEW_APP)
    # User already picked a different model on the NEW path
    target.setValue("model", "claude-opus-4-7")
    target.sync()

    _migrate(target, legacy)
    target.sync()

    # User's override survives; legacy key with no conflict is copied
    assert target.value("model") == "claude-opus-4-7"
    assert target.value("mode") == "browser"


def test_idempotent_via_flag():
    legacy = QSettings(TEST_ORG, LEGACY_APP)
    legacy.setValue("opacity", 0.5)
    legacy.sync()

    target = QSettings(TEST_ORG, NEW_APP)
    _migrate(target, legacy)
    target.sync()
    assert float(target.value("opacity")) == 0.5

    # Change the legacy value AFTER migration -- a re-run must not pick it up
    legacy.setValue("opacity", 0.9)
    legacy.sync()
    _migrate(target, legacy)
    target.sync()
    assert float(target.value("opacity")) == 0.5  # unchanged


def test_marks_flag_even_when_legacy_is_empty():
    # No legacy data: still flip the flag so we don't probe the registry on
    # every startup forever.
    target = QSettings(TEST_ORG, NEW_APP)
    legacy = QSettings(TEST_ORG, LEGACY_APP)
    assert not legacy.allKeys()

    _migrate(target, legacy)
    target.sync()

    assert target.value("migrated_from_wb3", type=bool) is True


def test_preserves_types_across_migration():
    """QSettings round-trips bool/int/float via the registry; verify they
    come back as the right Python types (callers rely on type=bool/type=float)."""
    legacy = QSettings(TEST_ORG, LEGACY_APP)
    legacy.setValue("always_on_top", False)
    legacy.setValue("desktop_max_steps", 120)
    legacy.setValue("opacity", 0.72)
    legacy.sync()

    target = QSettings(TEST_ORG, NEW_APP)
    _migrate(target, legacy)
    target.sync()

    # type= kwargs are what main.py uses to read these values
    assert target.value("always_on_top", type=bool) is False
    assert target.value("desktop_max_steps", type=int) == 120
    assert target.value("opacity", type=float) == 0.72
