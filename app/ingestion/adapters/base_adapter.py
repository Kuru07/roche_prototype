from abc import ABC, abstractmethod

class BaseAdapter(ABC):
    """Abstract interface for country-agnostic protocol adapters."""
    
    @abstractmethod
    def extract(self, source_meta: dict) -> list[dict]:
        """
        Extracts raw tabular dictionary records from an external or uploaded source.
        Returns a list of raw dictionaries prior to canonical normalization.
        """
        pass

