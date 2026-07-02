"""Base model for all platform data contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PlatformModel(BaseModel):
    """Common base for every cross-layer contract.

    - ``extra="forbid"``: reject unknown fields so a drifting producer fails
      loudly instead of silently dropping data.
    - ``populate_by_name=True``: allow constructing by field name even when an
      alias is declared (e.g. ``timestamp`` <-> DB column ``time``).
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )
