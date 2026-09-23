"""Excel report generator using openpyxl."""

import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


def _hs(cell):
    cell.font = Font(bold=True, size=11, color="FFFFFF")
    cell.fill = PatternFill(start_color="2196F3", end_color="2196F3", fill_type="solid")
    cell.alignment = Alignment(horizontal="center")


def _ss(cell):
    cell.font = Font(bold=True, size=11)
    cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")


def generate_excel_report(
    account_username, account_name, period_str,
    stats_summary, content_summary, daily_stats,
    publications, ai_analysis, dialogue_history=None,
    stories_summary=None, stories_list=None, comparison_periods=None,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 25

    ws["A1"] = f"Отчёт: @{account_username}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Период: {period_str}"

    row = 4
    for title, items in [
        ("Аккаунт", [("Имя", account_name), ("Username", f"@{account_username}")]),
        ("Статистика", [
            ("Подписчики", stats_summary.get("followers_end", "—")),
            ("Прирост", stats_summary.get("followers_growth", "—")),
            ("Прирост %", f'{stats_summary.get("followers_growth_pct", 0):.1f}%'),
            ("Охват", stats_summary.get("reach_total", "—")),
            ("Просмотры", stats_summary.get("views_total", "—")),
            ("Вовлечено", stats_summary.get("accounts_engaged_total", "—")),
            ("Ср. охват/день", stats_summary.get("reach_avg_daily", "—")),
        ]),
        ("Контент", [
            ("Публикаций", content_summary.get("total_posts", 0)),
            ("Reels", content_summary.get("total_reels", 0)),
            ("Видео", content_summary.get("total_videos", 0)),
            ("Фото", content_summary.get("total_images", 0)),
            ("Карусели", content_summary.get("total_carousels", 0)),
            ("Лайки", content_summary.get("total_likes", 0)),
            ("Комменты", content_summary.get("total_comments", 0)),
            ("Сохранения", content_summary.get("total_saves", 0)),
            ("Репосты", content_summary.get("total_shares", 0)),
            ("Ср. лайки", content_summary.get("avg_likes", 0)),
            ("Ср. охват", content_summary.get("avg_reach", 0)),
            ("ER %", content_summary.get("engagement_rate", 0)),
        ]),
    ]:
        ws[f"A{row}"] = title
        ws[f"B{row}"] = ""
        _ss(ws[f"A{row}"])
        _ss(ws[f"B{row}"])
        for label, value in items:
            row += 1
            ws[f"A{row}"] = label
            ws[f"B{row}"] = value
        row += 1

    ws[f"A{row}"] = "AI-анализ"
    _ss(ws[f"A{row}"])
    row += 1
    ws[f"A{row}"] = ai_analysis
    ws[f"A{row}"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 5, end_column=2)

    # ── Sheet 2: Daily Stats ──
    ws2 = wb.create_sheet("По дням")
    for col, h in enumerate(["Дата", "Охват", "Прирост", "Просмотры", "Вовлечено"], 1):
        _hs(ws2.cell(row=1, column=col, value=h))
        ws2.column_dimensions[get_column_letter(col)].width = 18
    for i, d in enumerate(daily_stats, 2):
        ws2.cell(row=i, column=1, value=d.get("date", ""))
        ws2.cell(row=i, column=2, value=d.get("reach", 0))
        ws2.cell(row=i, column=3, value=d.get("followers", 0))
        ws2.cell(row=i, column=4, value=d.get("views", 0))
        ws2.cell(row=i, column=5, value=d.get("accounts_engaged", 0))

    # ── Sheet 3: Publications ──
    ws3 = wb.create_sheet("Публикации")
    headers3 = ["Дата", "Тип", "Описание", "Лайки", "Комменты", "Сохран.", "Репосты", "Охват"]
    for col, h in enumerate(headers3, 1):
        _hs(ws3.cell(row=1, column=col, value=h))
    ws3.column_dimensions["A"].width = 14
    ws3.column_dimensions["B"].width = 14
    ws3.column_dimensions["C"].width = 50
    for c in "DEFGH":
        ws3.column_dimensions[c].width = 12
    tn = {"IMAGE": "Фото", "VIDEO": "Видео", "CAROUSEL_ALBUM": "Карусель", "REELS": "Reels"}
    row = 2
    for day in publications:
        for p in day.get("posts", []):
            ws3.cell(row=row, column=1, value=day.get("date", ""))
            ws3.cell(row=row, column=2, value=tn.get(p.get("type", ""), p.get("type", "")))
            ws3.cell(row=row, column=3, value=p.get("caption", "")[:100])
            ws3.cell(row=row, column=4, value=p.get("likes", 0))
            ws3.cell(row=row, column=5, value=p.get("comments", 0))
            ws3.cell(row=row, column=6, value=p.get("saved", 0))
            ws3.cell(row=row, column=7, value=p.get("shares", 0))
            ws3.cell(row=row, column=8, value=p.get("reach", 0))
            row += 1

    # ── Sheet 4: Stories ──
    if stories_summary:
        ws_s = wb.create_sheet("Сторис")
        ws_s["A1"] = "Сводка по сторис"
        _ss(ws_s["A1"])
        _ss(ws_s["B1"])
        for i, (label, key) in enumerate([
            ("Всего сторис", "total_stories"),
            ("Просмотры (итого)", "total_views"),
            ("Просмотры (средние)", "avg_views"),
            ("Охват (итого)", "total_reach"),
            ("Охват (средний)", "avg_reach"),
            ("Ответы", "total_replies"),
            ("Репосты", "total_shares"),
            ("Переходы в профиль", "total_profile_activity"),
            ("Подписки со сторис", "total_follows"),
            ("Выходы, %", "exit_rate"),
            ("Тапы вперёд", "tap_forward_total"),
            ("Тапы назад", "tap_back_total"),
        ], start=2):
            ws_s.cell(row=i, column=1, value=label)
            ws_s.cell(row=i, column=2, value=stories_summary.get(key, 0))
        ws_s.column_dimensions["A"].width = 24
        ws_s.column_dimensions["B"].width = 16

        header_row = 15
        headers_s = ["Дата", "Тип", "Просмотры", "Охват", "Ответы", "Репосты",
                     "Профиль", "Подписки", "Вперёд", "Назад", "Выходы", "Свайпы"]
        for col, h in enumerate(headers_s, 1):
            _hs(ws_s.cell(row=header_row, column=col, value=h))
        tn_s = {"IMAGE": "Фото", "VIDEO": "Видео"}
        for i, s in enumerate(stories_list or [], header_row + 1):
            ws_s.cell(row=i, column=1, value=s.get("date", ""))
            ws_s.cell(row=i, column=2, value=tn_s.get(s.get("media_type", ""), s.get("media_type", "")))
            ws_s.cell(row=i, column=3, value=s.get("views", 0))
            ws_s.cell(row=i, column=4, value=s.get("reach", 0))
            ws_s.cell(row=i, column=5, value=s.get("replies", 0))
            ws_s.cell(row=i, column=6, value=s.get("shares", 0))
            ws_s.cell(row=i, column=7, value=s.get("profile_activity", 0))
            ws_s.cell(row=i, column=8, value=s.get("follows", 0))
            ws_s.cell(row=i, column=9, value=s.get("tap_forward", 0))
            ws_s.cell(row=i, column=10, value=s.get("tap_back", 0))
            ws_s.cell(row=i, column=11, value=s.get("tap_exit", 0))
            ws_s.cell(row=i, column=12, value=s.get("swipe_forward", 0))

    # ── Sheet 5: Dialogue ──
    if dialogue_history:
        ws4 = wb.create_sheet("Диалог с AI")
        ws4.column_dimensions["A"].width = 14
        ws4.column_dimensions["B"].width = 100
        _hs(ws4.cell(row=1, column=1, value="Роль"))
        _hs(ws4.cell(row=1, column=2, value="Сообщение"))
        for i, msg in enumerate(dialogue_history, 2):
            ws4.cell(row=i, column=1, value="Вопрос" if msg.get("role") == "user" else "Ответ")
            c = ws4.cell(row=i, column=2, value=msg.get("content", ""))
            c.alignment = Alignment(wrap_text=True)

    if comparison_periods:
        ws_cmp = wb.create_sheet("Сравнение")
        headers = ["Показатель", "Период 1", "Период 2"]
        for col, header in enumerate(headers, 1):
            _hs(ws_cmp.cell(row=1, column=col, value=header))
        p1, p2 = comparison_periods
        metric_groups = [
            ("Статистика", [
                ("Прирост подписчиков", "followers_growth"),
                ("Охват", "reach_total"),
                ("Просмотры", "views_total"),
                ("Вовлечено", "accounts_engaged_total"),
            ], "stats"),
            ("Контент", [
                ("Публикации", "total_posts"),
                ("Лайки", "total_likes"),
                ("Комментарии", "total_comments"),
                ("ER %", "engagement_rate"),
            ], "content"),
            ("Stories", [
                ("Количество", "total_stories"),
                ("Просмотры", "total_views"),
                ("Ответы", "total_replies"),
            ], "stories"),
        ]
        row = 2
        for group, metrics, key in metric_groups:
            ws_cmp.cell(row=row, column=1, value=group)
            _ss(ws_cmp.cell(row=row, column=1))
            row += 1
            d1, d2 = p1.get(key, {}), p2.get(key, {})
            for label, metric in metrics:
                ws_cmp.cell(row=row, column=1, value=label)
                ws_cmp.cell(row=row, column=2, value=d1.get(metric, 0))
                ws_cmp.cell(row=row, column=3, value=d2.get(metric, 0))
                row += 1
        ws_cmp.column_dimensions["A"].width = 30
        ws_cmp.column_dimensions["B"].width = 18
        ws_cmp.column_dimensions["C"].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
