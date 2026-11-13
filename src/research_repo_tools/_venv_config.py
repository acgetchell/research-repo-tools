"""Parse unambiguous pyvenv.cfg fields without executing the interpreter."""

import io


def configuration_fields(configuration: str) -> dict[str, str]:
    """Normalize native keys and reject ambiguous repeated settings."""
    fields: dict[str, str] = {}
    # Match site.py's universal newlines and lower(), not Unicode casefolding
    # or splitlines(), which recognize extra keys or line boundaries.
    for line in io.StringIO(configuration, newline=None):
        key, separator, value = line.partition("=")
        if separator:
            key, value = key.strip().lower(), value.strip()
            if key in fields:
                raise ValueError("pyvenv.cfg contains duplicate settings")
            fields[key] = value
    return fields


def isolated_configuration(configuration: str) -> dict[str, str]:
    fields = configuration_fields(configuration)
    if fields.get("include-system-site-packages", "").lower() != "false":
        raise ValueError("pyvenv.cfg requires include-system-site-packages explicitly set to false")
    return fields
