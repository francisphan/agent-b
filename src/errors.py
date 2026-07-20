"""Shared exception types used across integration clients."""


class IntegrationDisabledError(RuntimeError):
    """An integration is switched off by configuration (e.g. PARDOT_TOOLS_ENABLED
    / OPERA_TOOLS_ENABLED off).

    Subclasses RuntimeError so existing broad handlers keep working, but lets
    composites (guest_360, person_brief) tell "intentionally disabled" apart from
    a real failure — a disabled leg is skipped, not degraded, so the usage report
    doesn't count config state as an error.
    """
