from __future__ import annotations

from datetime import datetime
from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def render_price_chart(
    prices: list[list[float]], symbol: str, days: int, currency: str = "USD"
) -> BytesIO:
    if not prices:
        raise ValueError("没有可绘制的行情数据")

    times = [datetime.fromtimestamp(point[0] / 1000) for point in prices]
    values = [point[1] for point in prices]
    rising = values[-1] >= values[0]
    color = "#16c784" if rising else "#ea3943"

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(10, 5), dpi=140)
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    ax.plot(times, values, color=color, linewidth=2)
    ax.fill_between(times, values, min(values), color=color, alpha=0.12)
    ax.set_title(f"{symbol} · {days}D · {currency.upper()}", fontsize=15, pad=14)
    ax.grid(alpha=0.12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d" if days > 1 else "%H:%M"))
    ax.tick_params(colors="#cbd5e1")
    fig.autofmt_xdate()
    fig.tight_layout()

    output = BytesIO()
    fig.savefig(output, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    output.seek(0)
    return output
