import pytest


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    return enable_custom_integrations
