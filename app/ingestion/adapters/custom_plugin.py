from app.ingestion.adapters.base_adapter import BaseAdapter

class CustomSourcePlugin(BaseAdapter):
    """
    Base class for source-specific plugins.
    Used exclusively when a public authority requires non-standard protocol handling
    (e.g., dynamic session handshakes, complex token authentication, or proprietary compression).
    """
    plugin_name: str = "generic_custom_plugin"
    
    def extract(self, source_meta: dict) -> list[dict]:
        raise NotImplementedError("Custom source plugins must implement extract()")

