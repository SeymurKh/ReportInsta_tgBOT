import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from config import settings

plt.style.use(settings.CHART_STYLE)
plt.rcParams.update({
    "figure.figsize": settings.CHART_SIZE,
    "axes.grid": True,
    "grid.alpha": 0.3,
})


def _to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def create_followers_chart(dates: list, followers: list) -> bytes:
    fig, ax = plt.subplots()
    available = [(day, value) for day, value in zip(dates, followers) if value is not None]
    colors = ["#4CAF50" if value >= 0 else "#F44336" for _, value in available]
    if available:
        ax.bar([day for day, _ in available], [value for _, value in available], color=colors, alpha=0.8)
    else:
        ax.text(0.5, 0.5, "Нет полных данных", ha="center", va="center", transform=ax.transAxes)
    ax.axhline(y=0, color="gray", linestyle="-", alpha=0.3)
    ax.set_title("Прирост подписчиков по дням", fontsize=14, fontweight="bold")
    ax.set_ylabel("Прирост")
    fig.autofmt_xdate()
    return _to_bytes(fig)


def create_metrics_chart(dates: list, reach: list) -> bytes:
    fig, ax = plt.subplots()
    ax.bar(dates, reach, color=settings.CHART_COLORS[1], alpha=0.8)
    ax.set_title("Охват по дням", fontsize=14, fontweight="bold")
    ax.set_ylabel("Охват")
    fig.autofmt_xdate()
    return _to_bytes(fig)


def create_stories_chart(dates: list, views: list) -> bytes:
    fig, ax = plt.subplots()
    ax.bar(dates, views, color="#9C27B0", alpha=0.8)
    if views:
        avg_val = sum(views) / len(views)
        ax.axhline(y=avg_val, color="red", linestyle="--", alpha=0.7, label=f"Среднее: {avg_val:.0f}")
        ax.legend()
    ax.set_title("Просмотры сторис по дням", fontsize=14, fontweight="bold")
    ax.set_ylabel("Просмотры")
    fig.autofmt_xdate()
    return _to_bytes(fig)


def create_comparison_chart(labels: list[str], p1_values: list, p2_values: list,
                            p1_name: str, p2_name: str) -> bytes:
    """Grouped bar chart comparing two periods by several metrics."""
    import numpy as np
    fig, ax = plt.subplots()
    x = np.arange(len(labels))
    width = 0.35
    values1 = np.array([np.nan if value is None else value for value in p1_values], dtype=float)
    values2 = np.array([np.nan if value is None else value for value in p2_values], dtype=float)
    ax.bar(x - width / 2, values1, width, label=p1_name, color=settings.CHART_COLORS[0], alpha=0.85)
    ax.bar(x + width / 2, values2, width, label=p2_name, color=settings.CHART_COLORS[1], alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_title("Сравнение периодов", fontsize=14, fontweight="bold")
    ax.legend()
    fig.tight_layout()
    return _to_bytes(fig)
