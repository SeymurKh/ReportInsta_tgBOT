"""T4 — E2E: real report/comparison generation against a snapshot copy.

Full user path (no Telegram sends): live Instagram refresh -> DB (copy) ->
calculations -> real OpenAI analysis -> text + charts + Excel.

Run:  set DATABASE_URL=sqlite+aiosqlite:///.../e2e_copy.db  then run this file.
"""
import asyncio
import os
import re
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.getcwd())

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main():
    from database.engine import init_db
    from database import crud
    from reports.generator import generate_report, generate_comparison_periods_report
    from reports.excel_generator import generate_excel_report
    from bot.helpers import build_excel_from_context

    await init_db()
    accounts = await crud.get_all_accounts()
    by_name = {a.username: a for a in accounts}
    db_path = os.environ["DATABASE_URL"].split("///")[-1]

    def sql(query, *args):
        con = sqlite3.connect(db_path)
        try:
            return con.execute(query, args).fetchall()
        finally:
            con.close()

    report_contexts = {}
    for name in ("biblioteka.baku", "roomfineartwinedine", "cms.space"):
        account = by_name[name]
        result = await generate_report(account, period="week")
        report_contexts[name] = result
        text, charts, context = result["text"], result["charts"], result["context"]
        check(f"report:{name}:text", len(text) > 500, f"{len(text)} chars")
        check(f"report:{name}:charts", isinstance(charts, dict) and len(charts) >= 1,
              f"{len(charts) if isinstance(charts, dict) else 0} charts")
        ai = context.get("ai_analysis") or ""
        check(f"report:{name}:ai_analysis", len(ai) > 100, f"{len(ai)} chars")

        stats = context["stats"]
        m = re.search(r"(\d{4}-\d{2}-\d{2})", context["period"])
        date_from = date.fromisoformat(m.group(1)) if m else None
        total_posts_sql = sql(
            "SELECT COUNT(*) FROM posts WHERE account_id=? AND date(timestamp) BETWEEN date(?) AND date(?)",
            account.id,
            str(date_from) if date_from else "1970-01-01", "2999-12-31")[0][0]
        print(f"  [{name}] reach_total={stats.get('reach_total')}, "
              f"followers_growth={stats.get('followers_growth')}, "
              f"views_total={stats.get('views_total')}, "
              f"posts_in_context={context['content'].get('total_posts')} "
              f"(sql>=date_from: {total_posts_sql})")

        # hard cross-check: context totals must exist in authoritative period snapshots
        week_rows = sql("SELECT period_start, period_end, reach, views FROM period_snapshots "
                        "WHERE account_id=? AND reach=?", account.id, stats.get("reach_total"))
        check(f"report:{name}:sql_reach_in_snapshots",
              any(r[2] == stats.get("reach_total") for r in week_rows) or stats.get("reach_total") is None,
              f"context reach={stats.get('reach_total')} vs snapshots {[(r[0], r[1], r[2]) for r in week_rows][:3]}")

    # custom period report (1 account)
    account = by_name["roomfineartwinedine"]
    result = await generate_report(
        account, period="custom", custom_date_from=date(2026, 9, 1),
        custom_date_to=date(2026, 9, 30))
    check("report:custom-september:text", len(result["text"]) > 500,
          f"{len(result['text'])} chars")
    check("report:custom-september:ai", len(result["context"].get("ai_analysis") or "") > 100)

    # comparison report (equal 14-day windows)
    account = by_name["biblioteka.baku"]
    comp = await generate_comparison_periods_report(
        account,
        period1_from=date(2026, 9, 1), period1_to=date(2026, 9, 14),
        period2_from=date(2026, 10, 1), period2_to=date(2026, 10, 14))
    check("comparison:text", len(comp["text"]) > 500, f"{len(comp['text'])} chars")
    check("comparison:ai", len(comp["context"].get("ai_analysis") or "") > 100)
    check("comparison:charts", len(comp["charts"]) >= 4, f"{len(comp['charts'])} charts")

    # Excel: bot path (from AI context) + direct generator path
    payload, filename = build_excel_from_context(
        report_contexts["biblioteka.baku"]["context"], None)
    check("excel:xlsx_magic", payload[:2] == b"PK", f"{filename}, {len(payload)} bytes")
    check("excel:size", len(payload) > 5000, f"{len(payload)} bytes")
    ctx = report_contexts["roomfineartwinedine"]["context"]
    xl = generate_excel_report(
        ctx["account_username"], ctx["account_name"], ctx["period"],
        ctx["stats"], ctx["content"], ctx["daily_stats"],
        ctx["publications"], ctx["ai_analysis"],
        stories_summary=ctx.get("stories"), stories_list=ctx.get("stories_list"),
        audience_data=ctx.get("audience"))
    check("excel:generator_direct", isinstance(xl, bytes) and len(xl) > 5000,
          f"{len(xl) if isinstance(xl, bytes) else 0} bytes")

    # save artifacts
    os.makedirs("audit_snapshot/artifacts", exist_ok=True)
    for name, res in list(report_contexts.items()):
        with open(f"audit_snapshot/artifacts/report_{name}.txt", "w", encoding="utf-8") as fh:
            fh.write(res["text"])
    with open("audit_snapshot/artifacts/report_custom_september.txt", "w", encoding="utf-8") as fh:
        fh.write(result["text"])
    with open("audit_snapshot/artifacts/comparison_sept_vs_oct.txt", "w", encoding="utf-8") as fh:
        fh.write(comp["text"])

    failed = [r for r in RESULTS if not r[1]]
    from instagram.client import close_shared_session
    await close_shared_session()
    print(f"\n=== T4 SUMMARY: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed, "
          f"{len(failed)} failed ===")
    for name, _, detail in failed:
        print(f"  FAILED: {name} {detail}")


if __name__ == "__main__":
    asyncio.run(main())

