"""Research-only integration services.

This package is deliberately separate from ``services.automated_trading``.  Its
adapters produce evidence and never own exchange, order, position, or scheduler
lifecycle.
"""
