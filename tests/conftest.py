import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Load custom integrations, with a real (in-memory) recorder running.

    recorder_mock has to be requested before the hass fixture, which
    enable_custom_integrations pulls in.
    """
    yield
