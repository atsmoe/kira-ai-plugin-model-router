"""KiraAI plugin entry point for deterministic model failover routing."""

from core.chat import KiraMessageBatchEvent
from core.plugin import BasePlugin, Priority, logger, on

from .router import ModelRouter


class ModelRouterPlugin(BasePlugin):
    """Build an ordered model group before KiraAI starts model execution."""

    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)
        self._router = ModelRouter(ctx, cfg, logger.warning)

    @on.im_batch_message(priority=Priority.HIGH)
    async def route_batch_message(self, event: KiraMessageBatchEvent):
        self._router.route(event)
