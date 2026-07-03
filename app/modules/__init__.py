"""
Import all ORM models so SQLAlchemy registers them.
"""

from app.modules.company.models import Company
from app.modules.project.models import Project

__all__ = [
    "Project",
    "Company",
]
