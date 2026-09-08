"""Independent Live kernel application, composed separately from the management Web."""

from .application import application, create_app

__all__ = ["application", "create_app"]
