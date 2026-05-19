"""Workflow definition dataclasses for Odoo business process state machines."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TransitionDef:
    """A valid state transition within a workflow.

    Represents a single action that moves a record from one state to another.
    Version constraints (``min_version`` / ``max_version``) let a single
    workflow definition advertise the correct method per Odoo major
    version — e.g. ``action_done`` for Odoo 17 vs ``action_lock`` for
    Odoo 18+ — without forcing the AI caller to know which version is
    in use. See :func:`is_supported_on` for the matching helper.
    """

    action: str
    """Method name to call, e.g. ``action_confirm``."""

    target_state: str
    """Target state value after the transition, e.g. ``sale``."""

    label: str
    """Human-readable label, e.g. ``Confirm Order``."""

    description: str = ""
    """Optional help text explaining the transition."""

    min_version: int | None = None
    """Minimum Odoo major version where this transition is supported.

    ``None`` means "no lower bound" (supported on every version). Use
    ``18`` to mark a transition introduced in Odoo 18 (e.g.
    ``action_lock`` replacing ``action_done``).
    """

    max_version: int | None = None
    """Maximum Odoo major version where this transition is supported.

    ``None`` means "no upper bound" (supported on every version). Use
    ``17`` to mark a transition removed in Odoo 18+ (e.g. legacy
    ``action_done`` on sale.order).
    """

    def is_supported_on(self, major_version: int | None) -> bool:
        """Return True if this transition applies to the given major version.

        When ``major_version`` is ``None`` (version not detected yet)
        the transition is advertised — failing open here is the right
        call because the caller has no version-specific advice to act
        on yet, and the static blocklist still filters out impossible
        method names elsewhere.
        """
        if major_version is None:
            return True
        if self.min_version is not None and major_version < self.min_version:
            return False
        if self.max_version is not None and major_version > self.max_version:
            return False
        return True


@dataclass(frozen=True)
class StateDef:
    """A workflow state with its outgoing transitions.

    Each state knows which transitions are available from it.
    """

    name: str
    """State value as stored in the database, e.g. ``draft``."""

    label: str
    """Human-readable label, e.g. ``Quotation``."""

    transitions: tuple[TransitionDef, ...] = ()
    """Outgoing transitions from this state."""


@dataclass(frozen=True)
class RelationHint:
    """Hint for filling a relational field when creating a record."""

    field_name: str
    """Field name, e.g. ``partner_id``."""

    relation_model: str
    """Related model, e.g. ``res.partner``."""

    hint: str
    """Human-readable hint, e.g. ``Search res.partner for customer``."""

    required: bool = True
    """Whether this relational field is required for creation."""


@dataclass(frozen=True)
class CreateGuide:
    """Guide for creating a record of a particular model.

    Provides hints beyond what ``fields_get`` tells you: which relations
    to resolve first, which fields to recommend, and whether the model
    has order lines.
    """

    required_relations: tuple[RelationHint, ...] = ()
    """Relations that should be resolved before creating the record."""

    recommended_fields: tuple[str, ...] = ()
    """Fields that are recommended (but not strictly required) to fill."""

    line_model: str | None = None
    """Child line model, e.g. ``sale.order.line``."""

    line_field: str | None = None
    """Field on the parent that holds lines, e.g. ``order_line``."""

    notes: str = ""
    """Free-form guidance for the AI agent."""


@dataclass(frozen=True)
class WorkflowDef:
    """Complete workflow definition for an Odoo model.

    Encodes the state machine (states + transitions), creation guidance,
    and version-specific notes.
    """

    model: str
    """Technical model name, e.g. ``sale.order``."""

    display_name: str
    """Human-readable name, e.g. ``Sales Order``."""

    state_field: str
    """Field that holds the workflow state, e.g. ``state``."""

    states: dict[str, StateDef]
    """Mapping of state value to ``StateDef``."""

    create_guide: CreateGuide | None = None
    """Optional creation guide."""

    version_notes: dict[str, str] = field(default_factory=dict)
    """Version-specific notes, keyed by major version string."""
