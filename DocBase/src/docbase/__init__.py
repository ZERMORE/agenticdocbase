"""Versioned evidence retrieval for doc2md outputs."""
__version__ = "0.1.0"


def build_index(*args, **kwargs):
    from .engine import build_index as implementation
    return implementation(*args, **kwargs)


def query(*args, **kwargs):
    from .engine import query as implementation
    return implementation(*args, **kwargs)
