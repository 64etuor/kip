from kip.cli import command_loads_models


def test_migration_does_not_construct_model_clients() -> None:
    assert command_loads_models("migrate") is False
    assert command_loads_models("search") is True
