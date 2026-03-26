from datetime import datetime, timezone

from news_sources import NewsArticle


def generate_html_report(
    articles: list[NewsArticle],
    client_matches: dict[str, str | None],
    lookback_hours: int,
) -> str:
    """Generate an HTML email report of process safety events.

    Args:
        articles: List of news articles to include.
        client_matches: Dict mapping article URL to matched client name (or None).
        lookback_hours: How far back the search covered.
    """
    match_count = sum(1 for v in client_matches.values() if v)
    period = f"{lookback_hours} hours" if lookback_hours <= 48 else f"{lookback_hours // 24} days"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = []
    for article in articles:
        date_str = article.date.strftime("%Y-%m-%d %H:%M") if article.date else "N/A"
        keywords_str = ", ".join(article.keywords_matched)
        client = client_matches.get(article.url)

        if client:
            client_cell = f'<span style="color:#1a7f37;font-weight:bold;">{_esc(client)}</span>'
            row_bg = "#f0fdf4"
        else:
            client_cell = '<span style="color:#888;">\u2014</span>'
            row_bg = "#ffffff"

        rows.append(f"""
        <tr style="background:{row_bg};">
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">{date_str}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">
                <a href="{_esc(article.url)}" style="color:#1d4ed8;text-decoration:none;">{_esc(article.title)}</a>
            </td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{_esc(article.source)}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{_esc(article.country)}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#666;">{_esc(keywords_str)}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center;">{client_cell}</td>
        </tr>""")

    rows_html = "\n".join(rows) if rows else f"""
        <tr>
            <td colspan="6" style="padding:24px;text-align:center;color:#888;">
                No process safety events found in the last {period}.
            </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background:#f3f4f6;">
    <div style="max-width:960px;margin:20px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <div style="background:#1e3a5f;color:#ffffff;padding:20px 24px;">
            <h1 style="margin:0;font-size:20px;font-weight:600;">Process Safety Events Monitor</h1>
            <p style="margin:6px 0 0;font-size:14px;opacity:0.85;">Report generated {now}</p>
        </div>

        <!-- Summary -->
        <div style="padding:16px 24px;background:#f8fafc;border-bottom:1px solid #e5e7eb;">
            <p style="margin:0;font-size:15px;color:#334155;">
                <strong>{len(articles)}</strong> process safety event(s) found in the last <strong>{period}</strong>.
                {f'<strong>{match_count}</strong> involve known clients.' if match_count else ''}
            </p>
        </div>

        <!-- Table -->
        <div style="overflow-x:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="background:#f1f5f9;">
                        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #cbd5e1;font-weight:600;color:#475569;">Date</th>
                        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #cbd5e1;font-weight:600;color:#475569;">Headline</th>
                        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #cbd5e1;font-weight:600;color:#475569;">Source</th>
                        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #cbd5e1;font-weight:600;color:#475569;">Country</th>
                        <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #cbd5e1;font-weight:600;color:#475569;">Keywords</th>
                        <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #cbd5e1;font-weight:600;color:#475569;">Client</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <!-- Footer -->
        <div style="padding:16px 24px;background:#f8fafc;border-top:1px solid #e5e7eb;font-size:12px;color:#94a3b8;">
            <p style="margin:0;">Acutech Consulting &mdash; Process Safety News Monitor</p>
            <p style="margin:4px 0 0;">This is an automated report. Contact nistre@acutech-consulting.com for questions.</p>
        </div>

    </div>
</body>
</html>"""


def _esc(text: str) -> str:
    """Basic HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
