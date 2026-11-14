"""Canonical public import name; preserves existing module and data identities."""

from nautionette.import_aliases import install_alias

install_alias(__name__)
