class ProviderError(RuntimeError):
    """Safe public failure; never includes prompts, responses or credentials."""


class BudgetExceeded(ProviderError):
    """A shared budget reservation was refused before contacting a provider."""


class BudgetConfigurationError(ProviderError):
    """Billing cannot safely be estimated with the configured policy."""
