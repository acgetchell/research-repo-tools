"""Research repository maintenance. Importing this package has no side effects."""

from importlib.metadata import version as _distribution_version

__version__ = _distribution_version("research-repo-tools")
