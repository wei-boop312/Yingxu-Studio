from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class QualityEvaluator(ABC):
    """Reserved interface; V2.0 does not bundle a recognition model."""

    @abstractmethod
    def evaluate(
        self, source_metadata: dict[str, Any], result_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError
