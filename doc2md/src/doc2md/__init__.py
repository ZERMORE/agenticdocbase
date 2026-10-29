"""Local document parsing with explicit provenance and optional vision."""

__version__ = "0.1.0"


def parse_document(*args, **kwargs):
    from .pipeline import parse_document as implementation
    return implementation(*args, **kwargs)


def parse_path(*args, **kwargs):
    from .pipeline import parse_path as implementation
    return implementation(*args, **kwargs)
