"""
Exception workflow: WASTE, ADJUST, UNFULFILLED handling.

DEPRECATED: This module redirects to ExceptionWorkflow from receiving.py.
Kept for backward compatibility.
"""
from .receiving import ExceptionWorkflow

__all__ = ['ExceptionWorkflow']
