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
    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in followers]
    ax.bar(dates, followers, color=colors, alpha=0.8)
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


def create_engagement_chart(dates: list, interactions: list) -> bytes:
    fig, ax = plt.subplots()
    ax.bar(dates, interactions, color=settings.CHART_COLORS[2], alpha=0.8)
    if interactions:
        avg_val = sum(interactions) / len(interactions)
        ax.axhline(y=avg_val, color="red", linestyle="--", alpha=0.7, label=f"Среднее: {avg_val:.0f}")
        ax.legend()
    ax.set_title("Взаимодействия по дням", fontsize=14, fontweight="bold")
    ax.set_ylabel("Взаимодействия")
    fig.autofmt_xdate()
    return _to_bytes(fig)


def create_content_comparison_chart(posts_data: list[dict]) -> bytes:
    fig, ax = plt.subplots()
    type_colors = {"IMAGE": "#2196F3", "VIDEO": "#4CAF50", "REELS": "#FF9800", "CAROUSEL_ALBUM": "#F44336"}
    for post in posts_data:
        color = type_colors.get(post["type"], "#999")
        ax.scatter(post["reach"], post["interactions"], c=color, s=80, alpha=0.7, edgecolors="white")
    for mtype, color in type_colors.items():
        ax.scatter([], [], c=color, label=mtype, s=80)
    ax.legend(title="Тип контента")
    ax.set_xlabel("Охват")
    ax.set_ylabel("Взаимодействия")
    ax.set_title("Эффективность контента", fontsize=14, fontweight="bold")
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
    ax.bar(x - width / 2, p1_values, width, label=p1_name, color=settings.CHART_COLORS[0], alpha=0.85)
    ax.bar(x + width / 2, p2_values, width, label=p2_name, color=settings.CHART_COLORS[1], alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_title("Сравнение периодов", fontsize=14, fontweight="bold")
    ax.legend()
    fig.tight_layout()
    return _to_bytes(fig)


def create_accounts_comparison_chart(accounts_data: list[dict]) -> bytes:
    fig, ax = plt.subplots()
    if not accounts_data:
        ax.set_title("Ð¡Ñ€Ð°Ð²Ð½ÐµÐ½Ð¸Ðµ Ð°ÐºÐºÐ°ÑƒÐ½Ñ‚Ð¾Ð²", fontsize=14, fontweight="bold")
        ax.text(0.5, 0.5, "ÐÐµÑ‚ Ð´Ð°Ð½Ð½Ñ‹Ñ…", ha="center", va="center")
        ax.set_axis_off()
        return _to_bytes(fig)
    names = [a["name"][:15] for a in accounts_data]
    followers = [a["followers"] for a in accounts_data]
    y_pos = range(len(names))
    bars = ax.barh(y_pos, followers, color=settings.CHART_COLORS[:len(names)])
    ax.set_yticks(y_pos)
    ax.set_yticklabels(names)
    ax.set_title("Сравнение аккаунтов", fontsize=14, fontweight="bold")
    ax.set_xlabel("Подписчики")
    for bar, val in zip(bars, followers):
        ax.text(bar.get_width() + max(followers) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=10)
    return _to_bytes(fig)
