"""Validate the visible distance screen and present SQL-returned results."""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display


def validate_radius(radius):
    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        raise ValueError("RADIUS_KM must be a finite number from 1 through 2000")
    if not math.isfinite(radius) or not 1 <= radius <= 2000:
        raise ValueError("RADIUS_KM must be a finite number from 1 through 2000")


def prose(text: str) -> None:
    display(Markdown(text))


def theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.dpi": 145,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#E8EDE9",
            "text.color": "#243B2F",
            "axes.labelcolor": "#405047",
            "text.parse_math": False,
            "legend.frameon": False,
        },
    )


def chart(fig, title: str, subtitle: str, source: str) -> None:
    fig.text(0.08, 0.98, title, ha="left", va="top", fontsize=17, fontweight="semibold")
    fig.text(0.08, 0.90, subtitle, ha="left", va="top", fontsize=10, color="#58685F")
    fig.tight_layout(rect=(0.02, 0.11, 1, 0.82))
    fig.text(0.08, 0.03, source, fontsize=8, color="#6C7D73")
    plt.show()
