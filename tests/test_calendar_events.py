"""重大事件预警日历的单元测试（纯逻辑，不打网络）。

覆盖: A股定期报告预约披露 / 分红送转除权除息 / 美港股 Yahoo calendarEvents / 倒计时分级。
"""

from datetime import date
from unittest.mock import patch

from src import calendar_events as ce
from src import fallback_sources as fb
from src.global_stock import parse_calendar_events
from src.models import CalendarEvent
from src.report import calendar_rows

TODAY = date(2026, 9, 26)


# ── 东财: 定期报告预约披露 ─────────────────────────────────────
def _appoint_row(code="600519", appoint="2026-10-31 00:00:00", published="0", changed=None):
    return {
        "SECURITY_CODE": code,
        "SECURITY_NAME_ABBR": "贵州茅台",
        "REPORT_TYPE_NAME": "2026年 三季报",
        "APPOINT_PUBLISH_DATE": appoint,
        "FIRST_CHANGE_DATE": changed,
        "SECOND_CHANGE_DATE": None,
        "THIRD_CHANGE_DATE": None,
        "IS_PUBLISH": published,
    }


class TestReportAppointments:
    def test_parses_appointed_date_and_report_name(self):
        with patch.object(fb, "em_datacenter", return_value=[_appoint_row()]):
            rows = fb.report_appointments(["600519"], date(2026, 9, 30))

        assert rows == [{"code": "600519", "name": "贵州茅台", "report_name": "2026年 三季报",
                         "appoint_date": "2026-10-31", "change_count": 0}]

    def test_skips_reports_already_published(self):
        with patch.object(fb, "em_datacenter", return_value=[_appoint_row(published="1")]):
            assert fb.report_appointments(["600519"], date(2026, 9, 30)) == []

    def test_counts_appointment_changes(self):
        row = _appoint_row(changed="2026-10-20 00:00:00")
        with patch.object(fb, "em_datacenter", return_value=[row]):
            assert fb.report_appointments(["600519"], date(2026, 9, 30))[0]["change_count"] == 1

    def test_filter_restricts_to_watchlist_codes_and_period(self):
        with patch.object(fb, "em_datacenter", return_value=[]) as m:
            fb.report_appointments(["600519", "000001"], date(2026, 9, 30))

        filter_ = m.call_args.kwargs["filter_"]
        assert "(REPORT_DATE='2026-09-30')" in filter_
        assert 'SECURITY_CODE in ("600519","000001")' in filter_

    def test_empty_codes_skip_request(self):
        with patch.object(fb, "em_datacenter") as m:
            assert fb.report_appointments([], date(2026, 9, 30)) == []
        m.assert_not_called()


# ── 东财: 分红送转 ─────────────────────────────────────────────
class TestDividendPlans:
    def test_parses_ex_dividend_and_record_date(self):
        row = {"SECURITY_CODE": "603390", "SECURITY_NAME_ABBR": "通达电气",
               "EX_DIVIDEND_DATE": "2026-09-28 00:00:00", "EQUITY_RECORD_DATE": "2026-09-24 00:00:00",
               "IMPL_PLAN_PROFILE": "10派1.00元(含税,扣税后0.90元)", "ASSIGN_PROGRESS": "实施分配"}
        with patch.object(fb, "em_datacenter", return_value=[row]):
            rows = fb.dividend_plans(["603390"], TODAY)

        assert rows == [{"code": "603390", "name": "通达电气", "ex_date": "2026-09-28",
                         "record_date": "2026-09-24", "plan": "10派1.00元(含税,扣税后0.90元)",
                         "progress": "实施分配"}]

    def test_filter_uses_ex_dividend_start_date(self):
        with patch.object(fb, "em_datacenter", return_value=[]) as m:
            fb.dividend_plans(["603390"], TODAY)

        assert "(EX_DIVIDEND_DATE>='2026-09-26')" in m.call_args.kwargs["filter_"]


# ── Yahoo calendarEvents ─────────────────────────────────────
class TestParseCalendarEvents:
    def test_extracts_earnings_date_estimate_flag_and_ex_dividend(self):
        data = {"calendarEvents": {
            "earnings": {"earningsDate": [{"raw": 1795507800, "fmt": "2026-11-24"}],
                         "isEarningsDateEstimate": True},
            "exDividendDate": {"raw": 1781049600, "fmt": "2026-06-10"}}}

        assert parse_calendar_events(data) == {
            "earnings_date": "2026-11-24", "earnings_is_estimate": True, "ex_dividend_date": "2026-06-10"}

    def test_missing_modules_yield_none(self):
        assert parse_calendar_events({}) == {
            "earnings_date": None, "earnings_is_estimate": False, "ex_dividend_date": None}


# ── 报告期推算 ─────────────────────────────────────────────────
class TestPendingReportPeriods:
    def test_late_september_waits_for_q3(self):
        assert ce.pending_report_periods(TODAY) == [date(2026, 9, 30)]

    def test_april_covers_annual_and_q1(self):
        assert ce.pending_report_periods(date(2026, 4, 10)) == [date(2025, 12, 31), date(2026, 3, 31)]

    def test_august_covers_half_year(self):
        assert ce.pending_report_periods(date(2026, 8, 5)) == [date(2026, 6, 30)]


# ── A股事件汇总 ────────────────────────────────────────────────
class TestAShareCorporateEvents:
    def test_report_dates_inside_window_become_events(self):
        rows = [{"code": "600519", "name": "贵州茅台", "report_name": "2026年 三季报",
                 "appoint_date": "2026-10-20", "change_count": 1},
                {"code": "600519", "name": "贵州茅台", "report_name": "2026年 三季报",
                 "appoint_date": "2026-12-30", "change_count": 0}]
        with patch.object(fb, "report_appointments", return_value=rows):
            events, warnings = ce._fetch_a_share_report_dates({"600519": "贵州茅台"}, today=TODAY)

        assert warnings == []
        assert [(e.event_date, e.category) for e in events] == [("2026-10-20", "业绩披露")]
        assert "已改期1次" in events[0].detail

    def test_report_date_failure_degrades_to_warning(self):
        with patch.object(fb, "report_appointments", side_effect=RuntimeError("403")):
            events, warnings = ce._fetch_a_share_report_dates({"600519": "贵州茅台"}, today=TODAY)

        assert events == [] and "业绩披露日历" in warnings[0]

    def test_dividends_inside_window_become_events(self):
        rows = [{"code": "603390", "name": "通达电气", "ex_date": "2026-09-28", "record_date": "2026-09-24",
                 "plan": "10派1.00元", "progress": "实施分配"}]
        with patch.object(fb, "dividend_plans", return_value=rows):
            events, _ = ce._fetch_a_share_dividends({"603390": "通达电气"}, today=TODAY)

        assert events[0].category == "除权除息"
        assert "10派1.00元" in events[0].detail


# ── 美股/港股 Yahoo 事件 ───────────────────────────────────────
class TestGlobalCorporateEvents:
    def test_hk_earnings_and_dividends_in_window(self):
        info = {"0700.HK": {"earnings_date": "2026-10-15", "earnings_is_estimate": True,
                            "ex_dividend_date": "2026-10-01"}}
        with patch.object(ce, "_yahoo_calendar", side_effect=lambda s, m: info.get(s)):
            events, _ = ce._fetch_global_corporate_events(
                {"00700": ("腾讯", "港股")}, us_earnings_covered=set(), today=TODAY)

        assert sorted((e.event_date, e.category) for e in events) == [
            ("2026-10-01", "除权除息"), ("2026-10-15", "财报")]
        assert any("预估" in e.detail for e in events)

    def test_us_earnings_skipped_when_nasdaq_already_has_it(self):
        info = {"AAPL": {"earnings_date": "2026-10-01", "earnings_is_estimate": False,
                         "ex_dividend_date": None}}
        with patch.object(ce, "_yahoo_calendar", side_effect=lambda s, m: info.get(s)):
            events, _ = ce._fetch_global_corporate_events(
                {"AAPL": ("苹果", "美股")}, us_earnings_covered={"AAPL"}, today=TODAY)

        assert events == []


# ── 报告倒计时分级 ─────────────────────────────────────────────
class TestCalendarRows:
    def _event(self, d):
        return CalendarEvent(event_date=d, category="财报", symbol="AAPL", name="苹果", detail="财报")

    def test_countdown_and_urgency_marks(self):
        rows = calendar_rows([self._event("2026-09-28"), self._event("2026-10-02"),
                              self._event("2026-10-20")], today=TODAY)

        assert [(r["days_left"], r["urgency"]) for r in rows] == [(2, "🔴"), (6, "🟡"), (24, "")]

    def test_past_events_are_dropped(self):
        assert calendar_rows([self._event("2026-09-20")], today=TODAY) == []

    def test_unparseable_date_kept_without_countdown(self):
        rows = calendar_rows([self._event("待定")], today=TODAY)
        assert rows[0]["days_left"] is None
