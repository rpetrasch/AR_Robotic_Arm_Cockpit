from django.conf import settings


def test_tests_run_in_the_test_stage():
    """Unit tests never use the live Redis (a running cockpit's status cache, channel layer)."""
    assert settings.TESTING
    assert settings.CACHES["default"]["BACKEND"].endswith("LocMemCache")
    assert settings.CHANNEL_LAYERS["default"]["BACKEND"].endswith("InMemoryChannelLayer")
