from __future__ import annotations


def tab_label(chart_id: int | None, chart_name: str, index: int) -> str:
    # Drill By sends slice_id 0, so a falsy ID means the chart has no identity.
    if not chart_id:
        return chart_name if index == 0 else f"{chart_name} ({index})"
    prefix = str(chart_id) if index == 0 else f"{chart_id}.{index}"
    return f"{prefix} - {chart_name}"
