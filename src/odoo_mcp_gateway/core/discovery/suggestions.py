"""Model suggestions: search, categorise, and suggest related Odoo models."""

from __future__ import annotations

from .model_registry import ModelRegistry
from .models import ModelInfo

# Mapping of category names to keyword tokens that match model names.
CATEGORIES: dict[str, list[str]] = {
    "sales": ["sale", "crm", "quotation", "invoice", "payment"],
    "inventory": ["stock", "warehouse", "picking", "product", "location"],
    "hr": ["hr", "employee", "attendance", "leave", "department"],
    "project": ["project", "task", "milestone", "timesheet"],
    "accounting": ["account", "move", "payment", "tax", "journal"],
    "purchase": ["purchase", "vendor", "supplier"],
    "helpdesk": ["ticket", "helpdesk", "support"],
    "manufacturing": ["mrp", "production", "bom", "workorder"],
}


class ModelSuggestions:
    """High-level search, categorisation, and suggestion layer."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: str, is_admin: bool = False) -> list[ModelInfo]:
        """Search accessible models by free-text query.

        The query is matched against model names and descriptions as well
        as category keywords.
        """
        if not query:
            return []

        q = query.lower()
        accessible = self._registry.get_accessible_models(is_admin=is_admin)

        # Direct name / description match.
        results: list[ModelInfo] = []
        seen: set[str] = set()
        for m in accessible:
            if q in m.name.lower() or q in m.description.lower():
                results.append(m)
                seen.add(m.name)

        # Category keyword expansion: if query matches a category keyword,
        # include models whose names contain any keyword in that category.
        expanded_keywords: set[str] = set()
        for _cat, keywords in CATEGORIES.items():
            if any(q in kw or kw in q for kw in keywords):
                expanded_keywords.update(keywords)

        if expanded_keywords:
            for m in accessible:
                if m.name not in seen:
                    name_lower = m.name.lower()
                    if any(kw in name_lower for kw in expanded_keywords):
                        results.append(m)
                        seen.add(m.name)

        return results

    def get_by_category(
        self,
        category: str,
        is_admin: bool = False,
    ) -> list[ModelInfo]:
        """Return accessible models belonging to *category*."""
        keywords = CATEGORIES.get(category.lower(), [])
        if not keywords:
            return []

        accessible = self._registry.get_accessible_models(is_admin=is_admin)
        return [m for m in accessible if any(kw in m.name.lower() for kw in keywords)]

    def get_categories(self, is_admin: bool = False) -> dict[str, int]:
        """Return category names with counts of matching accessible models."""
        all_models = self._registry.get_accessible_models(is_admin=is_admin)
        result: dict[str, int] = {}
        for cat, keywords in CATEGORIES.items():
            count = sum(
                1 for m in all_models if any(kw in m.name.lower() for kw in keywords)
            )
            result[cat] = count
        return result

    def suggest_related(
        self, model_name: str, is_admin: bool = False
    ) -> list[ModelInfo]:
        """Suggest models related to *model_name*.

        Heuristic: models that share the same dotted prefix
        (e.g. ``sale.order`` and ``sale.order.line`` share ``sale``).
        """
        parts = model_name.split(".")
        if not parts:
            return []

        prefix = parts[0]
        accessible = self._registry.get_accessible_models(is_admin=is_admin)
        return [
            m
            for m in accessible
            if m.name != model_name and m.name.startswith(prefix + ".")
        ]
