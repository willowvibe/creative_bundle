"""Local data visualization renderer using matplotlib."""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_DPI = 150


class MatplotlibDataVizProvider(GenerativeProvider):
    """Render charts and simple data visualizations locally."""

    provider_name = "matplotlib_data_viz"

    def __init__(
        self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT, dpi: int = DEFAULT_DPI
    ):
        self.width = width
        self.height = height
        self.dpi = dpi

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a chart from the structured payload in ``request.params``."""
        # Lazy-import matplotlib so a missing dependency only breaks this
        # provider, not the whole registry (Bug #109).
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        data = request.params.get("data", {})
        chart_type = data.get("chart_type", "line")
        title = data.get("title", "")
        labels = data.get("labels", [])
        values = data.get("values", [])
        x_label = data.get("x_label", "")
        y_label = data.get("y_label", "")
        color = data.get("color", "#00D8FF")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        fig, ax = plt.subplots(
            figsize=(self.width / self.dpi, self.height / self.dpi), dpi=self.dpi
        )
        fig.patch.set_alpha(0.0)
        ax.set_facecolor("none")

        if chart_type == "bar":
            ax.bar(labels, values, color=color)
        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%")
        else:
            ax.plot(labels, values, color=color, linewidth=3.0)

        if title:
            ax.set_title(title, color="white", fontsize=18)
        if x_label:
            ax.set_xlabel(x_label, color="white")
        if y_label:
            ax.set_ylabel(y_label, color="white")

        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_color("white")

        plt.tight_layout()
        fig.savefig(str(output_path), transparent=True, bbox_inches="tight", dpi=self.dpi)
        plt.close(fig)

        logger.info(
            "data_viz_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            chart_type=chart_type,
            path=str(output_path),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.DATA_VIZ,
            local_path=str(output_path),
            metadata={
                "provider": self.provider_name,
                "chart_type": chart_type,
                "width": self.width,
                "height": self.height,
            },
        )
