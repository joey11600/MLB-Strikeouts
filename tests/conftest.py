def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: exercises the real Statcast cache; deselect with -m 'not slow'",
    )
