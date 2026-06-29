#!/usr/bin/env python3
"""
香港天文台 — 各區過去雨量記錄爬蟲v0.13

從 https://www.weather.gov.hk/tc/wxinfo/rainfall/rf_record.shtml
下載指定地區、指定時段（預設 08–17 時）的歷史雨量，輸出 CSV。

用法：
    python hko_islands_rainfall_scraper.py          # 開啟圖形介面
    python hko_islands_rainfall_scraper.py --gui    # 開啟圖形介面
    python hko_islands_rainfall_scraper.py --district 離島區 --start 2026-05-01 --end 2026-05-31
"""

import argparse
import calendar
import csv
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable, List, Optional, Tuple

BASE_URL = "https://www.weather.gov.hk/dps/wxinfo/rainfall/archive/rf_record_{timestamp}00c.htm"
DEFAULT_DISTRICT = "請選擇"
DISTRICTS = [
    "請選擇",
    "中西區",
    "灣仔",
    "東區",
    "南區",
    "油尖旺",
    "深水埗",
    "九龍城",
    "黃大仙",
    "觀塘",
    "荃灣",
    "屯門",
    "元朗",
    "北區",
    "大埔",
    "西貢",
    "沙田",
    "葵青",
    "離島區",
]
ROW_PATTERN = re.compile(
    r"<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>",
    re.IGNORECASE,
)
HEADERS = {
    "User-Agent": "HKO-Rainfall-Scraper/1.0",
    "Accept": "text/html,*/*",
}
CSV_FIELDS = ["日期", "時間", "地區", "雨量", "狀態"]
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "hko_rainfall_exports")
SPONSOR_URL = "https://payme.hsbc/a1edf2fd63d34a53bbc78e9c034a59c8"
SPONSOR_MESSAGE = (
    "如果這個工具對你有幫助，歡迎贊助作者 Howardwhs，"
    "支持後續維護與功能更新。"
)
GITHUB_URL = "https://github.com/Howardwhs0/HKO_Rainfall_Downloader"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def district_keys(name: str) -> set:
    normalized = normalize_text(name)
    keys = {normalized}
    if normalized.endswith("區"):
        keys.add(normalized[:-1])
    else:
        keys.add(normalized + "區")
    return keys


def districts_match(html_name: str, selected: str) -> bool:
    return bool(district_keys(html_name) & district_keys(selected))


def normalize_rainfall(text: str) -> str:
    return normalize_text(text)


def parse_district_rainfall(html: str, district: str) -> Tuple[Optional[str], str]:
    if "現時並無錄得雨量記錄" in html:
        return None, "無雨量記錄"

    for name, rainfall in ROW_PATTERN.findall(html):
        if districts_match(name, district):
            return normalize_rainfall(rainfall), "有資料"

    if "各區錄得雨量如下" in html or "地區" in html:
        return None, f"{district}未列出"

    return None, "無資料"


def fetch_rainfall_record(
    dt: datetime, district: str, timeout: int = 20
) -> Tuple[Optional[str], str]:
    timestamp = dt.strftime("%Y%m%d%H")
    url = BASE_URL.format(timestamp=timestamp)
    req = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "未能提供記錄"
        return None, f"HTTP錯誤({exc.code})"
    except urllib.error.URLError as exc:
        return None, f"連線錯誤({exc.reason})"

    return parse_district_rainfall(html, district)


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_output_filename(
    district: str,
    start_date: date,
    end_date: date,
    hour_start: int,
    hour_end: int,
) -> str:
    return (
        f"{district}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
        f"_{hour_start:02d}-{hour_end:02d}時.csv"
    )


def default_output_name(
    district: str,
    start_date: date,
    end_date: date,
    hour_start: int,
    hour_end: int,
) -> str:
    return os.path.join(
        OUTPUT_DIR,
        build_output_filename(district, start_date, end_date, hour_start, hour_end),
    )


def resolve_output_path(path: str) -> str:
    path = path.strip()
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.abspath(path)


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def default_past_month_range(today: Optional[date] = None) -> Tuple[date, date]:
    today = today or date.today()
    return today - timedelta(days=29), today


def scrape(
    start_date: date,
    end_date: date,
    hours: range,
    delay: float,
    output_path: str,
    district: str,
    *,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    hour_list = list(hours)
    total = ((end_date - start_date).days + 1) * len(hour_list)
    rows: List[dict] = []
    done = 0
    cancelled = False
    output_path = resolve_output_path(output_path)
    write_error = ""

    try:
        ensure_parent_dir(output_path)
        csv_file = open(output_path, "w", newline="", encoding="utf-8-sig")
    except OSError as exc:
        return {
            "total_rows": 0,
            "with_data": 0,
            "output_path": output_path,
            "cancelled": False,
            "completed": False,
            "saved": False,
            "error": f"無法建立 CSV 檔案：{exc}",
        }

    with csv_file as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        f.flush()

        for day in date_range(start_date, end_date):
            for hour in hour_list:
                if should_cancel and should_cancel():
                    cancelled = True
                    break

                dt = datetime(day.year, day.month, day.day, hour)
                rainfall, status = fetch_rainfall_record(dt, district)
                row = {
                    "日期": day.isoformat(),
                    "時間": f"{hour:02d}:00",
                    "地區": district,
                    "雨量": rainfall or "",
                    "狀態": status,
                }
                rows.append(row)
                try:
                    writer.writerow(row)
                    f.flush()
                except OSError as exc:
                    write_error = f"寫入 CSV 失敗：{exc}"
                    cancelled = True
                    break

                done += 1
                label = rainfall or status
                message = f"[{done:4d}/{total}] {day} {hour:02d}:00 → {label}"
                if on_progress:
                    on_progress(done, total, message)
                if delay > 0:
                    time.sleep(delay)

            if cancelled:
                break

    with_data = sum(1 for r in rows if r["狀態"] == "有資料")
    saved = bool(rows) and not write_error
    return {
        "total_rows": len(rows),
        "with_data": with_data,
        "output_path": output_path,
        "cancelled": cancelled and not write_error,
        "completed": not cancelled and bool(rows),
        "saved": saved,
        "error": write_error,
    }


class CalendarDialog:
    WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

    def __init__(
        self,
        parent: tk.Misc,
        target_var: tk.StringVar,
        title: str = "選擇日期",
        on_select: Optional[Callable[[], None]] = None,
    ):
        self.parent = parent
        self.target_var = target_var
        self.title = title
        self.on_select = on_select
        self.popup: Optional[tk.Toplevel] = None
        self.day_buttons: List[ttk.Button] = []
        self.year_var = tk.IntVar()
        self.month_var = tk.IntVar()

    def _parse_initial_date(self) -> date:
        try:
            return datetime.strptime(self.target_var.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            return date.today()

    def show(self, anchor: Optional[tk.Widget] = None) -> None:
        if self.popup and self.popup.winfo_exists():
            self.popup.lift()
            self.popup.focus_force()
            return

        initial = self._parse_initial_date()
        self.year_var.set(initial.year)
        self.month_var.set(initial.month)

        self.popup = tk.Toplevel(self.parent)
        self.popup.title(self.title)
        self.popup.resizable(False, False)
        self.popup.transient(self.parent.winfo_toplevel())
        self.popup.grab_set()

        frame = ttk.Frame(self.popup, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(frame)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(header, text="◀", width=3, command=self._prev_month).pack(side=tk.LEFT)
        self.month_label = ttk.Label(header, font=("Microsoft JhengHei UI", 11, "bold"))
        self.month_label.pack(side=tk.LEFT, expand=True)
        ttk.Button(header, text="▶", width=3, command=self._next_month).pack(side=tk.RIGHT)

        weekday_row = ttk.Frame(frame)
        weekday_row.pack(fill=tk.X)
        for name in self.WEEKDAYS:
            ttk.Label(
                weekday_row,
                text=name,
                width=4,
                anchor=tk.CENTER,
                font=("Microsoft JhengHei UI", 9, "bold"),
            ).pack(side=tk.LEFT, padx=1)

        self.days_frame = ttk.Frame(frame)
        self.days_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        footer = ttk.Frame(frame)
        footer.pack(fill=tk.X)
        ttk.Button(footer, text="今天", command=self._select_today).pack(side=tk.LEFT)
        ttk.Button(footer, text="取消", command=self._close).pack(side=tk.RIGHT)

        self.popup.bind("<Escape>", lambda _e: self._close())
        self._render_days()
        self._position_near(anchor)
        self.popup.wait_window()

    def _position_near(self, anchor: Optional[tk.Widget]) -> None:
        self.popup.update_idletasks()
        if anchor is not None:
            x = anchor.winfo_rootx()
            y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        else:
            x = self.parent.winfo_rootx() + 40
            y = self.parent.winfo_rooty() + 40
        self.popup.geometry(f"+{x}+{y}")

    def _update_month_label(self) -> None:
        self.month_label.configure(
            text=f"{self.year_var.get()}年 {self.month_var.get()}月"
        )

    def _render_days(self) -> None:
        for btn in self.day_buttons:
            btn.destroy()
        self.day_buttons.clear()
        self._update_month_label()

        weeks = calendar.monthcalendar(self.year_var.get(), self.month_var.get())

        for week in weeks:
            row = ttk.Frame(self.days_frame)
            row.pack(fill=tk.X, pady=1)
            for day in week:
                if day == 0:
                    ttk.Label(row, text="", width=4).pack(side=tk.LEFT, padx=1)
                    continue

                picked = date(self.year_var.get(), self.month_var.get(), day)
                btn = ttk.Button(
                    row,
                    text=str(day),
                    width=4,
                    command=lambda d=picked: self._select_date(d),
                )
                btn.pack(side=tk.LEFT, padx=1)
                self.day_buttons.append(btn)

    def _shift_month(self, delta: int) -> None:
        month = self.month_var.get() + delta
        year = self.year_var.get()
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        self.year_var.set(year)
        self.month_var.set(month)
        self._render_days()

    def _prev_month(self) -> None:
        self._shift_month(-1)

    def _next_month(self) -> None:
        self._shift_month(1)

    def _select_date(self, picked: date) -> None:
        self.target_var.set(picked.isoformat())
        if self.on_select:
            self.on_select()
        self._close()

    def _select_today(self) -> None:
        self._select_date(date.today())

    def _close(self) -> None:
        if self.popup and self.popup.winfo_exists():
            self.popup.grab_release()
            self.popup.destroy()
        self.popup = None


class RainfallScraperApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("香港天文台各區雨量下載器")
        self.root.minsize(640, 520)
        self.root.geometry("720x620")

        self._worker: Optional[threading.Thread] = None
        self._cancel_flag = False

        start_default, end_default = default_past_month_range()
        self.district_var = tk.StringVar(value=DEFAULT_DISTRICT)
        self.start_var = tk.StringVar(value=start_default.isoformat())
        self.end_var = tk.StringVar(value=end_default.isoformat())
        self.hour_start_var = tk.IntVar(value=8)
        self.hour_end_var = tk.IntVar(value=17)
        self.delay_var = tk.DoubleVar(value=0.3)
        self.output_var = tk.StringVar(
            value=default_output_name(
                DEFAULT_DISTRICT, start_default, end_default, 8, 17
            )
        )
        self.status_var = tk.StringVar(value="就緒")
        self._output_dir_override: Optional[str] = None
        self._active_calendar: Optional[CalendarDialog] = None

        self._build_ui()
        self._bind_output_name_updates()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            header,
            text="香港天文台各區雨量記錄下載 v0.13 by Howardwhs",
            font=("Microsoft JhengHei UI", 14, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Button(header, text="☕ 贊助", command=self._open_sponsor).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        ttk.Button(header, text="GitHub", command=self._open_github).pack(
            side=tk.RIGHT
        )

        ttk.Label(
            main,
            text="資料來源：weather.gov.hk 過去各區雨量記錄",
            foreground="#555",
        ).pack(anchor=tk.W, pady=(0, 12))

        form = ttk.LabelFrame(main, text="下載設定", padding=10)
        form.pack(fill=tk.X, pady=(0, 10))

        row0 = ttk.Frame(form)
        row0.pack(fill=tk.X, pady=4)
        ttk.Label(row0, text="地區", width=10).pack(side=tk.LEFT)
        district_box = ttk.Combobox(
            row0,
            textvariable=self.district_var,
            values=DISTRICTS,
            width=12,
            state="readonly",
        )
        district_box.pack(side=tk.LEFT)

        row1 = ttk.Frame(form)
        row1.pack(fill=tk.X, pady=4)
        ttk.Label(row1, text="開始日期", width=10).pack(side=tk.LEFT)
        self.start_entry = ttk.Entry(
            row1, textvariable=self.start_var, width=14, state="readonly"
        )
        self.start_entry.pack(side=tk.LEFT)
        ttk.Button(
            row1,
            text="📅",
            width=3,
            command=lambda: self._open_calendar(self.start_var, "選擇開始日期", self.start_entry),
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row1, text="結束日期", width=10).pack(side=tk.LEFT, padx=(12, 0))
        self.end_entry = ttk.Entry(
            row1, textvariable=self.end_var, width=14, state="readonly"
        )
        self.end_entry.pack(side=tk.LEFT)
        ttk.Button(
            row1,
            text="📅",
            width=3,
            command=lambda: self._open_calendar(self.end_var, "選擇結束日期", self.end_entry),
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(row1, text="過去30天", command=self._set_past_month).pack(
            side=tk.LEFT, padx=(12, 0)
        )
        for entry, var, title in (
            (self.start_entry, self.start_var, "選擇開始日期"),
            (self.end_entry, self.end_var, "選擇結束日期"),
        ):
            entry.bind(
                "<Button-1>",
                lambda _e, v=var, t=title, w=entry: self._open_calendar(v, t, w),
            )

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=4)
        ttk.Label(row2, text="開始時段", width=10).pack(side=tk.LEFT)
        ttk.Spinbox(
            row2, from_=0, to=23, textvariable=self.hour_start_var, width=6
        ).pack(side=tk.LEFT)
        ttk.Label(row2, text="時").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row2, text="結束時段", width=10).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Spinbox(row2, from_=0, to=23, textvariable=self.hour_end_var, width=6).pack(
            side=tk.LEFT
        )
        ttk.Label(row2, text="時（含）").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row2, text="請求間隔", width=10).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Spinbox(
            row2,
            from_=0.0,
            to=5.0,
            increment=0.1,
            textvariable=self.delay_var,
            width=6,
        ).pack(side=tk.LEFT)
        ttk.Label(row2, text="秒").pack(side=tk.LEFT, padx=(4, 0))

        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=4)
        ttk.Label(row3, text="輸出 CSV", width=10).pack(side=tk.LEFT)
        ttk.Entry(row3, textvariable=self.output_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Button(row3, text="瀏覽…", command=self._browse_output).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=(0, 10))
        self.start_btn = ttk.Button(btn_row, text="開始下載", command=self._start_download)
        self.start_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(
            btn_row, text="取消", command=self._cancel_download, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.open_btn = ttk.Button(
            btn_row, text="開啟輸出資料夾", command=self._open_output_dir, state=tk.DISABLED
        )
        self.open_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._last_output_path = ""

        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 8))

        log_frame = ttk.LabelFrame(main, text="下載記錄", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = scrolledtext.ScrolledText(
            log_frame, height=14, state=tk.DISABLED, font=("Consolas", 10)
        )
        self.log.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, textvariable=self.status_var, foreground="#333").pack(
            anchor=tk.W, pady=(6, 0)
        )

    def _open_sponsor(self) -> None:
        if SPONSOR_URL.strip():
            webbrowser.open(SPONSOR_URL.strip())
            return

        messagebox.showinfo("贊助作者", SPONSOR_MESSAGE)

    def _open_github(self) -> None:
        if GITHUB_URL.strip():
            webbrowser.open(GITHUB_URL.strip())
            return

        messagebox.showinfo("GitHub", "尚未設定 GitHub 專案連結。")

    def _open_calendar(
        self,
        target_var: tk.StringVar,
        title: str,
        anchor: tk.Widget,
    ) -> None:
        if self._worker and self._worker.is_alive():
            return

        self._active_calendar = CalendarDialog(
            self.root,
            target_var,
            title=title,
            on_select=self._refresh_output_name,
        )
        self._active_calendar.show(anchor)

    def _bind_output_name_updates(self):
        def refresh(_a=None, _b=None, _c=None):
            self._refresh_output_name()

        for var in (
            self.district_var,
            self.start_var,
            self.end_var,
            self.hour_start_var,
            self.hour_end_var,
        ):
            var.trace_add("write", refresh)

        for entry in (self.start_entry, self.end_entry):
            entry.bind("<FocusOut>", refresh)

    def _set_past_month(self):
        start, end = default_past_month_range()
        self.start_var.set(start.isoformat())
        self.end_var.set(end.isoformat())

    def _refresh_output_name(self):
        try:
            start = datetime.strptime(self.start_var.get().strip(), "%Y-%m-%d").date()
            end = datetime.strptime(self.end_var.get().strip(), "%Y-%m-%d").date()
            district = self.district_var.get().strip() or DEFAULT_DISTRICT
            hour_start = int(self.hour_start_var.get())
            hour_end = int(self.hour_end_var.get())
            filename = build_output_filename(
                district, start, end, hour_start, hour_end
            )
            out_dir = self._output_dir_override or OUTPUT_DIR
            self.output_var.set(os.path.join(out_dir, filename))
        except (ValueError, tk.TclError):
            pass

    def _browse_output(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        self._refresh_output_name()
        initial_name = os.path.basename(self.output_var.get().strip()) or "雨量資料.csv"
        initial_dir = self._output_dir_override or OUTPUT_DIR
        path = filedialog.asksaveasfilename(
            title="儲存 CSV",
            defaultextension=".csv",
            filetypes=[("CSV 檔案", "*.csv"), ("所有檔案", "*.*")],
            initialdir=initial_dir,
            initialfile=initial_name,
        )
        if path:
            self._output_dir_override = os.path.dirname(path)
            self.output_var.set(path)

    def _open_output_dir(self):
        target = self._last_output_path or self.output_var.get().strip()
        if not target:
            return
        folder = os.path.dirname(resolve_output_path(target)) or OUTPUT_DIR
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            subprocess.run(["xdg-open", folder], check=False)

    def _append_log(self, text: str):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _validate_inputs(
        self,
    ) -> Optional[Tuple[date, date, range, float, str, str]]:
        district = self.district_var.get().strip()
        if not district:
            messagebox.showerror("地區錯誤", "請選擇地區。")
            return None

        try:
            start_date = datetime.strptime(self.start_var.get().strip(), "%Y-%m-%d").date()
            end_date = datetime.strptime(self.end_var.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("日期錯誤", "請使用 YYYY-MM-DD 格式，例如 2026-05-01")
            return None

        if start_date > end_date:
            messagebox.showerror("日期錯誤", "開始日期不可晚於結束日期。")
            return None

        hour_start = self.hour_start_var.get()
        hour_end = self.hour_end_var.get()
        if hour_start > hour_end:
            messagebox.showerror("時段錯誤", "開始時段不可晚於結束時段。")
            return None

        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("輸出錯誤", "請指定 CSV 輸出路徑。")
            return None

        return (
            start_date,
            end_date,
            range(hour_start, hour_end + 1),
            self.delay_var.get(),
            resolve_output_path(output),
            district,
        )

    def _set_running(self, running: bool):
        state = tk.DISABLED if running else tk.NORMAL
        self.start_btn.configure(state=state)
        self.cancel_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _start_download(self):
        if self._worker and self._worker.is_alive():
            return

        params = self._validate_inputs()
        if not params:
            return

        start_date, end_date, hours, delay, output_path, district = params
        total = ((end_date - start_date).days + 1) * len(list(hours))

        self._cancel_flag = False
        self.progress.configure(value=0, maximum=total)
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
        self._append_log(f"地區：{district}")
        self._append_log(
            f"下載範圍：{start_date} 至 {end_date}，"
            f"時段 {hours.start:02d}–{hours.stop - 1:02d} 時，共 {total} 筆"
        )
        self._append_log(f"輸出：{output_path}")
        self.status_var.set("下載中…")
        self._set_running(True)
        self.open_btn.configure(state=tk.DISABLED)
        self._last_output_path = output_path

        def worker():
            def on_progress(done, total_count, message):
                self.root.after(
                    0,
                    lambda d=done, t=total_count, m=message: self._on_progress(d, t, m),
                )

            try:
                result = scrape(
                    start_date,
                    end_date,
                    hours,
                    delay,
                    output_path,
                    district,
                    on_progress=on_progress,
                    should_cancel=lambda: self._cancel_flag,
                )
            except Exception as exc:
                result = {
                    "total_rows": 0,
                    "with_data": 0,
                    "output_path": output_path,
                    "cancelled": False,
                    "completed": False,
                    "saved": False,
                    "error": f"下載過程發生錯誤：{exc}",
                }
            self.root.after(0, lambda r=result: self._on_finished(r))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_progress(self, done: int, total: int, message: str):
        self.progress.configure(value=done, maximum=total)
        self._append_log(message)
        self.status_var.set(f"下載中… {done}/{total}")

    def _on_finished(self, result: dict):
        self._set_running(False)
        self.progress.configure(value=self.progress["maximum"])
        self._last_output_path = result.get("output_path", "")

        if result.get("error"):
            self.status_var.set("匯出失敗")
            msg = result["error"]
            self._append_log(f"\n{msg}")
            messagebox.showerror("匯出失敗", msg)
            return

        if result.get("saved"):
            self.open_btn.configure(state=tk.NORMAL)

        if result["cancelled"]:
            self.status_var.set("已取消")
            if result.get("saved"):
                msg = (
                    f"下載已中止，已儲存部分資料（{result['total_rows']} 筆）。\n"
                    f"檔案位置：{result['output_path']}"
                )
                self._append_log(f"\n{msg}")
                messagebox.showinfo("已取消", msg)
            else:
                self._append_log("\n下載已取消，未儲存任何資料。")
                messagebox.showinfo("已取消", "下載已中止，未儲存任何資料。")
            return

        if result["completed"]:
            msg = (
                f"完成：共 {result['total_rows']} 筆，"
                f"其中 {result['with_data']} 筆有雨量資料。\n"
                f"已儲存至：{result['output_path']}"
            )
            self.status_var.set("下載完成")
            self._append_log(f"\n{msg}")
            messagebox.showinfo("下載完成", msg)
        else:
            self.status_var.set("下載失敗")
            self._append_log("\n下載失敗，未取得任何資料。")
            messagebox.showerror("下載失敗", "未取得任何資料，請檢查網路或日期設定。")

    def _cancel_download(self):
        self._cancel_flag = True
        self.status_var.set("正在取消…")


def run_gui():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    RainfallScraperApp(root)
    root.mainloop()


def parse_args():
    start_default, end_default = default_past_month_range()
    parser = argparse.ArgumentParser(
        description="下載香港天文台各區過去雨量記錄並輸出 CSV"
    )
    parser.add_argument("--gui", action="store_true", help="開啟圖形介面")
    parser.add_argument(
        "--district",
        default=DEFAULT_DISTRICT,
        choices=DISTRICTS,
        help=f"地區（預設：{DEFAULT_DISTRICT}）",
    )
    parser.add_argument("--start", default=start_default.isoformat())
    parser.add_argument("--end", default=end_default.isoformat())
    parser.add_argument("--hour-start", type=int, default=8)
    parser.add_argument("--hour-end", type=int, default=17)
    parser.add_argument("--output", default="")
    parser.add_argument("--delay", type=float, default=0.3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.gui or len(sys.argv) == 1:
        run_gui()
        return 0

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    if start_date > end_date:
        print("錯誤：開始日期不可晚於結束日期。", file=sys.stderr)
        return 1

    hours = range(args.hour_start, args.hour_end + 1)
    output = resolve_output_path(
        args.output
        or default_output_name(
            args.district, start_date, end_date, args.hour_start, args.hour_end
        )
    )

    def on_progress(done, total, message):
        print(message)

    print(f"地區：{args.district}")
    print(f"輸出：{output}")
    result = scrape(
        start_date,
        end_date,
        hours,
        args.delay,
        output,
        args.district,
        on_progress=on_progress,
    )

    if result.get("error"):
        print(f"\n匯出失敗：{result['error']}", file=sys.stderr)
        return 1

    if result["completed"]:
        print(
            f"\n完成：共 {result['total_rows']} 筆，"
            f"其中 {result['with_data']} 筆有雨量資料。\n"
            f"已儲存至：{result['output_path']}"
        )
        return 0

    if result.get("saved"):
        print(
            f"\n已儲存部分資料（{result['total_rows']} 筆）至：{result['output_path']}",
            file=sys.stderr,
        )
        return 1

    print("\n下載失敗。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
