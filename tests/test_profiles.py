from workpilot.profiles import PROFILE_VERSION, register_workpilot_profiles
from workpilot.settings import Settings


def test_harness_profile_is_versioned_and_idempotent() -> None:
    settings = Settings.from_env(allow_placeholder=True)
    first = register_workpilot_profiles(settings)
    second = register_workpilot_profiles(settings)
    assert first == second
    assert first.version == PROFILE_VERSION
    assert len(first.sha256) == 64
