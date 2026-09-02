"""Universal Hermes email plugin foundation."""

from .config import EmailPluginConfig, load_config
from .context import ActiveProfileContextSource, HermesContext, HermesContextSource

__all__ = [
    "ActiveProfileContextSource",
    "EmailPluginConfig",
    "HermesContext",
    "HermesContextSource",
    "load_config",
]
__version__ = "0.4.0"
