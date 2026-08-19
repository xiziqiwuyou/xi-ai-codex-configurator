class ConfiguratorError(Exception):
    """Base error that is safe to present to a user."""


class DiscoveryError(ConfiguratorError):
    pass


class DesktopControlError(ConfiguratorError):
    pass


class CredentialError(ConfiguratorError):
    pass


class RemoteModelError(ConfiguratorError):
    pass


class CatalogError(ConfiguratorError):
    pass


class ConfigurationError(ConfiguratorError):
    pass


class SessionMigrationError(ConfiguratorError):
    pass


class TransactionError(ConfiguratorError):
    pass


class BackupSpaceError(TransactionError):
    """The selected backup volume cannot safely hold the transaction backup."""

    pass
