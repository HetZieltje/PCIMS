"""Stable exception vocabulary shared by the PCIMS data layer."""


class ValidationError(ValueError):
    """A requested operation violates a domain rule."""


class NotFoundError(LookupError):
    """A requested domain record does not exist."""


class SchemaVersionError(RuntimeError):
    """The database is not the exact schema supported by this build."""


class DatabaseIntegrityError(RuntimeError):
    """Stored records violate structural or semantic integrity."""
