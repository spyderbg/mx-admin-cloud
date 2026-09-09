"""Per-request cost estimation from a provider's configured price table.

The price table is plain config, so operators can correct prices without a code
change. An unknown model yields ``None`` rather than ``0.0`` so that gaps are
visible in ``requests.log`` instead of silently reading as free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _to_price(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PriceTable:
    """USD price lookup keyed by model, then by ``size|quality``."""

    per_model: dict[str, Any]
    cost_per_image: float | None = None

    @classmethod
    def from_config(cls, pricing: Any, cost_per_image: Any = None) -> "PriceTable":
        """Build a price table from ``config.pricing`` and ``config.cost_per_image``."""
        table: dict[str, Any] = {}
        if isinstance(pricing, dict):
            for model, entry in pricing.items():
                key = str(model).strip()
                if not key:
                    continue
                if isinstance(entry, dict):
                    variants = {
                        str(variant).strip(): price
                        for variant, raw in entry.items()
                        if (price := _to_price(raw)) is not None
                    }
                    if variants:
                        table[key] = variants
                    continue
                price = _to_price(entry)
                if price is not None:
                    table[key] = price
        return cls(per_model=table, cost_per_image=_to_price(cost_per_image))

    def estimate(self, model: str | None, size: str | None, quality: str | None) -> float | None:
        """Return the estimated USD cost of one image, or ``None`` when unpriced."""
        entry = self.per_model.get((model or "").strip())
        price = self._from_entry(entry, size, quality)
        if price is not None:
            return price

        default_entry = self.per_model.get("default")
        price = self._from_entry(default_entry, size, quality)
        if price is not None:
            return price

        return self.cost_per_image

    @staticmethod
    def _from_entry(entry: Any, size: str | None, quality: str | None) -> float | None:
        if entry is None:
            return None
        if isinstance(entry, dict):
            for key in (f"{size}|{quality}", size, quality, "default"):
                if key is None:
                    continue
                price = _to_price(entry.get(str(key)))
                if price is not None:
                    return price
            return None
        return _to_price(entry)

    def as_dict(self) -> dict[str, Any]:
        """Return the table as a plain dict for API responses."""
        payload: dict[str, Any] = dict(self.per_model)
        if self.cost_per_image is not None:
            payload["cost_per_image"] = self.cost_per_image
        return payload
