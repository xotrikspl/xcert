"""Terminal rendering for xCert.

Three looks share one data model: ledger (quiet table), timeline (90-day axis)
and console (htop-like bars). Colours come from per-look 24-bit palettes, fall
back to the 16 ANSI colours, or disappear for journald, cron and NO_COLOR.
Every screen fits in 80 columns and avoids emoji, whose width terminals disagree on.
"""
import math
import os
import re
import shutil
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from i18n import t

THEMES = ("ledger", "timeline", "console")
COLOR_MODES = ("auto", "full", "16", "none")
BACKGROUNDS = ("dark", "light")
DEFAULT_APPEARANCE = {"theme": "ledger", "colors": "auto", "background": "dark"}

# Screen size. Layouts are designed for 80 columns; on a wider terminal fit_screen() stretches bars,
# rules and text columns, and ROWS (0 = unlimited, as for pipes and tests) scrolls long lists.
MIN_WIDTH, MAX_WIDTH = 80, 160
WIDTH = 80
INNER = WIDTH - 2
ROWS = 0
TERM_COLS = 0  # real terminal width when it is narrower than MIN_WIDTH, 0 otherwise
CRITICAL_DAYS = 7
AXIS_DAYS = 90
AXIS_COLS = 45  # two days per column keeps the timeline inside 80 columns
NAME_W = 25

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
EIGHTHS = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# ======================
# PALETTES
# ======================
PALETTES: Dict[str, Dict[str, Dict]] = {
    "ledger": {  # ivory and brass
        "dark": {
            "fg": "#e4dfd3", "accent": "#cfae6b", "ok": "#9dc7a0", "warn": "#e0b562", "err": "#e8838f",
            "info": "#9bb8c9", "grad": ("#9dc7a0", "#b9c98e", "#e0b562", "#e59f72", "#e8838f"),
            "bar": "#2a2620", "okbg": "#22342a", "warnbg": "#3a3020", "errbg": "#3d2427",
        },
        "light": {
            "fg": "#2b2822", "accent": "#8a6a2a", "ok": "#3f7d4e", "warn": "#9a6b12", "err": "#b23a48",
            "info": "#2f6680", "grad": ("#3f7d4e", "#6b7f2e", "#9a6b12", "#b0582a", "#b23a48"),
            "bar": "#ece6d8", "okbg": "#dcebdd", "warnbg": "#f3e6c8", "errbg": "#f5d9dc",
        },
    },
    "timeline": {  # azure
        "dark": {
            "fg": "#dce3ee", "accent": "#7aa7ff", "ok": "#6fd3b0", "warn": "#e6cf6a", "err": "#ee7d86",
            "info": "#7fd0e6", "grad": ("#6fd3b0", "#a6d98a", "#e6cf6a", "#efa36a", "#ee7d86"),
            "bar": "#1e2635", "okbg": "#173329", "warnbg": "#35301a", "errbg": "#3a1f25",
        },
        "light": {
            "fg": "#1f2633", "accent": "#2f5fd0", "ok": "#16855f", "warn": "#8f7300", "err": "#c2384a",
            "info": "#1c7c99", "grad": ("#16855f", "#4f8a1e", "#8f7300", "#b35a12", "#c2384a"),
            "bar": "#e6ecf6", "okbg": "#d6f0e5", "warnbg": "#f4ebc4", "errbg": "#f8d9de",
        },
    },
    "console": {  # indigo
        "dark": {
            "fg": "#daddf0", "accent": "#9aa6ff", "ok": "#8fe0bc", "warn": "#f0d58a", "err": "#f5a3ad",
            "info": "#93c5f5", "grad": ("#8fe0bc", "#b8de8f", "#f0d58a", "#f3b07e", "#f5a3ad"),
            "bar": "#2b2f4a", "okbg": "#1e4a3a", "warnbg": "#4a3f1e", "errbg": "#52232c",
        },
        "light": {
            "fg": "#22253a", "accent": "#4351c9", "ok": "#1c7a55", "warn": "#8a6a00", "err": "#b83246",
            "info": "#2465a8", "grad": ("#1c7a55", "#4e7f1c", "#8a6a00", "#ad5419", "#b83246"),
            "bar": "#e3e6f7", "okbg": "#d3eee2", "warnbg": "#f3e8c4", "errbg": "#f7d7dd",
        },
    },
}
ANSI_FG = {"fg": "39", "ok": "32", "warn": "33", "err": "31", "info": "34",
           "g1": "32", "g2": "32", "g3": "33", "g4": "33", "g5": "31"}
ANSI_ACCENT = {"ledger": "39", "timeline": "36", "console": "36"}
ANSI_CHIP = {"bar": "7", "ok": "30;42", "warn": "30;43", "err": "30;41"}
CHIP_BG = {"bar": "bar", "ok": "okbg", "warn": "warnbg", "err": "errbg"}


def _rgb(hex_color: str) -> str:
    return ";".join(str(int(hex_color[i:i + 2], 16)) for i in (1, 3, 5))


def resolve_color_mode(requested: str, stream=None, assume_terminal: bool = False,
                       env: Optional[Dict[str, str]] = None) -> str:
    """Picks the colour mode actually used for this run.

    NO_COLOR and non-terminal output (journald, cron, pipes) always win, so a
    saved "full" never leaks escape codes into logs. `assume_terminal` is for
    MOTD, whose output is captured by pam_motd and then shown on a terminal.
    """
    env = os.environ if env is None else env
    if env.get("NO_COLOR"):
        return "none"
    stream = stream or sys.stdout
    is_tty = assume_terminal or (hasattr(stream, "isatty") and stream.isatty())
    if not is_tty or env.get("TERM") == "dumb":
        return "none"
    if requested in ("full", "16", "none"):
        return requested
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") or env.get("WT_SESSION"):
        return "full"
    return "16"


class Painter:
    """Wraps text in SGR codes for the chosen look, colour mode and background."""

    def __init__(self, theme: str = "ledger", mode: str = "none", background: str = "dark"):
        self.theme = theme if theme in THEMES else "ledger"
        self.mode = mode if mode in ("full", "16", "none") else "none"
        self.background = background if background in BACKGROUNDS else "dark"
        self.pal = PALETTES[self.theme][self.background]

    def _sgr(self, codes: str, text: str) -> str:
        if self.mode == "none" or not codes or not text:
            return text
        return f"\033[{codes}m{text}\033[0m"

    def _fg_code(self, role: str) -> str:
        if self.mode == "full":
            color = self.pal["grad"][int(role[1]) - 1] if role in ANSI_FG and role[0] == "g" else self.pal[role]
            return "38;2;" + _rgb(color)
        return ANSI_ACCENT[self.theme] if role == "accent" else ANSI_FG[role]

    def fg(self, role: str, text: str, bold: bool = False) -> str:
        codes = self._fg_code(role)
        return self._sgr("1;" + codes if bold else codes, text)

    def bold(self, text: str) -> str:
        return self._sgr("1", text)

    def dim(self, text: str) -> str:
        return self._sgr("2", text)

    def chip(self, role: str, text: str, accent: bool = False) -> str:
        """Text on a tinted background, used by the console look's bars."""
        if self.mode == "full":
            fg = self.pal["accent"] if accent else self.pal["fg" if role == "bar" else role]
            codes = f"48;2;{_rgb(self.pal[CHIP_BG[role]])};38;2;{_rgb(fg)}"
            return self._sgr("1;" + codes if accent else codes, text)
        return self._sgr(ANSI_CHIP[role] + (";1" if accent else ""), text)


# ======================
# DATA MODEL
# ======================
@dataclass
class CertState:
    name: str
    provider: str
    sans: List[str] = field(default_factory=list)
    present: bool = False
    expires: Optional[datetime] = None
    days_left: Optional[int] = None
    reload: str = ""


@dataclass
class Context:
    version: str
    host: str
    now: datetime
    threshold: int = 30
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None  # start of the last timer run
    last_ok: bool = True
    running: bool = False  # a timer run is renewing right now
    states: List[CertState] = field(default_factory=list)  # for the console title counters


def category(s: CertState, threshold: int) -> str:
    if not s.present:
        return "missing"
    if s.days_left is None:
        return "unreadable"
    if s.days_left < 0:
        return "expired"
    if s.days_left < CRITICAL_DAYS:
        return "critical"
    return "due" if s.days_left <= threshold else "ok"


def level(s: CertState, threshold: int) -> str:
    return {"ok": "ok", "due": "warn"}.get(category(s, threshold), "err")


def grad_role(days: Optional[int], threshold: int) -> str:
    if days is None or days <= CRITICAL_DAYS:
        return "g5"
    if days <= 14:
        return "g4"
    if days <= threshold:
        return "g3"
    return "g2" if days <= 60 else "g1"


def status_text(s: CertState) -> str:
    cat = category(s, 10 ** 6)
    if cat == "missing":
        return t("no certificate")
    if cat == "unreadable":
        return t("certificate unreadable")
    if cat == "expired":
        return t("expired {n} days ago", n=-s.days_left)
    return t("{n} days left", n=s.days_left)


def short_status(s: CertState) -> str:
    cat = category(s, 10 ** 6)
    return {"missing": t("none"), "unreadable": t("n/a"), "expired": t("expired")}.get(cat, f"{s.days_left} d")


COUNT_LABELS = (("ok", "ok", "ok"), ("due", "due", "warn"), ("critical", "expiring", "err"),
                ("expired", "expired", "err"), ("missing", "missing", "err"), ("unreadable", "unreadable", "err"))


def counts(states: Sequence[CertState], threshold: int) -> List[Tuple[int, str, str]]:
    """Returns (count, label, level) for every non-empty category, in a fixed order."""
    cats = [category(s, threshold) for s in states]
    return [(cats.count(c), label, lvl) for c, label, lvl in COUNT_LABELS if cats.count(c)]


def plan_runs(states: Sequence[CertState], ctx: Context, limit: int = 3) -> List[Tuple[datetime, List[CertState]]]:
    """Groups certificates by the timer run that will renew or issue them."""
    if ctx.next_run is None:
        return []
    # ponytail: assumes the weekly cadence of xcert.timer; read OnCalendar if the schedule becomes configurable
    first = (ctx.next_run - ctx.now).total_seconds() / 86400
    plan: Dict[datetime, List[CertState]] = {}
    for s in states:
        due = 0 if s.days_left is None or s.days_left <= ctx.threshold else s.days_left - ctx.threshold
        weeks = 0 if first >= due else math.ceil((due - first) / 7)
        plan.setdefault(ctx.next_run + timedelta(days=7 * weeks), []).append(s)
    return sorted(plan.items(), key=lambda kv: kv[0])[:limit]


# ======================
# TEXT HELPERS
# ======================
def visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - visible_len(text))


def rpad(text: str, width: int) -> str:
    return " " * max(0, width - visible_len(text)) + text


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def row(left: str, right: str, width: Optional[int] = None) -> str:
    width = INNER if width is None else width
    return left + " " * max(1, width - visible_len(left) - visible_len(right)) + right


def day(dt: datetime) -> str:
    return f"{t(WEEKDAYS[dt.weekday()])} {dt.day}.{dt.month}"


def run_label(ctx: Context) -> str:
    if ctx.running:
        return t("renewal running now")
    if not ctx.next_run:
        return t("timer not active")
    return t("next run {when}", when=f"{day(ctx.next_run)} {ctx.next_run:%H:%M}")


def run_alert(p: Painter, ctx: Context, indent: str = "") -> List[str]:
    """A red line wherever the timer shows up when its last run failed, so a failure is not missed."""
    if ctx.last_ok or ctx.last_run is None or ctx.running:
        return []
    when = f"{day(ctx.last_run)} {ctx.last_run:%H:%M}"
    return [indent + p.fg("err", "! " + t("last timer run failed ({when}), see {cmd}", when=when, cmd="xcert --status"))]


def duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


def smooth_bar(fraction: float, width: int) -> str:
    eighths = round(max(0.0, min(1.0, fraction)) * width * 8)
    return "█" * (eighths // 8) + EIGHTHS[eighths % 8]


def wrap_join(items: Sequence[Tuple[str, str]], width: int, indent: int) -> List[str]:
    """Joins (plain, painted) items with commas, wrapping on visible width."""
    lines, cur, cur_len = [], "", 0
    for plain, painted in items:
        piece_len = len(plain) + (2 if cur else 0)
        if cur and cur_len + piece_len > width:
            lines.append(cur + ",")
            cur, cur_len = " " * indent + painted, indent + len(plain)
        else:
            cur += (", " if cur else "") + painted
            cur_len += piece_len
    return lines + ([cur] if cur else [])


def _counts_line(p: Painter, states: Sequence[CertState], ctx: Context, sep: str = "    ") -> str:
    parts = []
    for n, label, lvl in counts(states, ctx.threshold):
        dot = "○" if label == "missing" else "●"
        parts.append(p.fg(lvl, dot) + f" {n} {t(label)}")
    return sep.join(parts) if parts else p.dim(t("no certificates configured"))


def _failures(p: Painter, results: Sequence[Tuple[str, bool]], indent: str = "") -> List[str]:
    failed = [label for label, ok in results if not ok]
    if not failed:
        return []
    return ["", indent + t("failed:") + " " + ", ".join(p.fg("err", f) for f in failed),
            indent + t("check {validate} and the error log, then run {auto}",
                       validate=p.fg("accent", "xcert --validate-config", bold=True),
                       auto=p.fg("accent", "xcert --auto", bold=True))]


def _mark(p: Painter, on: bool) -> str:
    """Cursor marker for the row under the arrow keys; a space keeps the columns put."""
    return p.fg("accent", "▸") if on else " "


def _hint(p: Painter, back: bool = False) -> str:
    text = t("↑↓ move   Enter select   q back") if back else t("↑↓ move   Enter select")
    return _bar(p, [("bar", " " + text + " ", False), None]) if p.theme == "console" else "  " + p.dim(text)


def _room(fixed: int) -> int:
    """Lines left for a scrolling list once `fixed` lines are drawn; 0 means no limit."""
    return max(3, ROWS - 1 - fixed) if ROWS else 0


def _window(p: Painter, lines: List[str], sel: Optional[int], room: int) -> List[str]:
    """The rows that fit in `room` lines, scrolled to keep `sel` in view, with counts of the hidden ones."""
    if not room or len(lines) <= room:
        return lines
    body = room - 2
    start = 0 if sel is None else min(max(0, sel - body // 2), len(lines) - body)
    below = len(lines) - start - body
    return ([p.dim("    ↑ " + t("{n} more", n=start)) if start else ""] + lines[start:start + body]
            + [p.dim("    ↓ " + t("{n} more", n=below)) if below else ""])


def _action_rows(p: Painter, items, sel: Optional[int] = None, first: int = 0) -> List[str]:
    """One `key  label` row per item; `first` is the cursor index of the first row."""
    return [f" {_mark(p, first + i == sel)}{p.fg('accent', key, bold=True)}   "
            + (p.bold(label) if first + i == sel else label) for i, (key, label, _) in enumerate(items)]


def _head(p: Painter, ctx: Context, sub: str) -> List[str]:
    """Screen title in the current look; long titles (a domain name) are cut to leave room for the run time."""
    sub = clip(sub, 28)
    if p.theme == "ledger":
        return _ledger_header(p, ctx, sub)
    if p.theme == "timeline":
        return [row(p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version) + "   " + p.bold(sub),
                    p.dim(run_label(ctx)))]
    return [_title(p, ctx, ctx.states, sub)]


# ======================
# LEDGER
# ======================
def _ledger_rule(p: Painter) -> str:
    return p.dim("─" * INNER)


def _ledger_header(p: Painter, ctx: Context, sub: str = "") -> List[str]:
    left = p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version) + ("   " + p.bold(sub) if sub else "")
    return [row(left, p.dim(f"{ctx.host}  {ctx.now:%d.%m %H:%M}")), _ledger_rule(p)]


def ledger_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = _ledger_header(p, ctx, t("certificates"))
    out.append("  " + p.dim(pad(t("certificate"), 26) + pad(t("provider"), 13) + pad(t("expires"), 11)
                            + rpad(t("days"), 4) + "  " + t("after renewal")))
    for s in states:
        lvl = level(s, ctx.threshold)
        if s.expires is None:
            mid = p.fg("err", pad(clip(status_text(s), 15), 15))
        else:
            mid = pad(f"{s.expires:%d.%m.%Y}", 11) + p.fg(lvl, rpad(str(s.days_left), 4), bold=True)
        out.append(p.fg(lvl, "○" if not s.present else "●") + " " + pad(clip(s.name, 25), 26)
                   + p.dim(pad(clip(s.provider, 12), 13)) + mid + "  " + p.dim(clip(s.reload, INNER - 58)))
        out.extend("  " + p.dim("+ " + san) for san in s.sans)
    out += [_ledger_rule(p), _counts_line(p, states, ctx, ", "),
            p.dim(t("renewal threshold {n} days", n=ctx.threshold) + ", " + run_label(ctx))]
    return "\n".join(out)


def ledger_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    ok = sum(1 for _, s in results if s)
    left = p.fg("ok", t("renewed {n}", n=ok)) + "   " + (p.fg("err", t("failed {n}", n=len(results) - ok))
                                                          if ok < len(results) else p.dim(t("failed {n}", n=0)))
    return "\n".join([_ledger_rule(p), row(left, p.dim(duration(elapsed)))] + _failures(p, results))


def ledger_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    out = _ledger_header(p, ctx)
    out += ["  " + _counts_line(p, states, ctx), "  " + p.dim(run_label(ctx))] + run_alert(p, ctx, "  ") + [""]
    out += _action_rows(p, items, sel)
    return "\n".join(out + ["", _hint(p)])


def ledger_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    head = p.fg("accent", "xCert", bold=True) + "  "
    if not bad:
        return row(head + p.fg("ok", t("{n} certificates OK", n=len(states))), p.dim(run_label(ctx)))
    out = [row(head + t("{n} certificates", n=len(states)) + ", " + p.fg("err", t("{n} need attention", n=len(bad))),
               p.dim(run_label(ctx)))]
    out += [f"  {p.fg('err', '○' if not s.present else '●')} {pad(clip(s.name, 25), 26)} {status_text(s)}" for s in bad]
    return "\n".join(out + ["  " + p.dim(t("details:") + " xcert --list")])


# ======================
# TIMELINE
# ======================
def _runs(cells: Sequence[Tuple[str, str]], p: Painter) -> str:
    """Paints consecutive cells sharing a role in one escape sequence."""
    out, i = [], 0
    while i < len(cells):
        j = i
        while j < len(cells) and cells[j][0] == cells[i][0]:
            j += 1
        text = "".join(c for _, c in cells[i:j])
        role = cells[i][0]
        out.append(p.dim(text) if role == "dim" else p.fg(role, text))
        i = j
    return "".join(out)


def _run_cols(ctx: Context) -> List[int]:
    if ctx.next_run is None:
        return []
    first = (ctx.next_run - ctx.now).total_seconds() / 86400
    return [round((first + 7 * k) / 2) for k in range(14) if 0 <= first + 7 * k < AXIS_DAYS]


def _lane(p: Painter, s: CertState, ctx: Context) -> str:
    lvl = level(s, ctx.threshold)
    head = p.fg(lvl, "○" if not s.present else "●") + " " + pad(clip(s.name, NAME_W - 1), NAME_W)
    d = s.days_left
    if d is None or d < 0:
        mark = p.dim("·" * AXIS_COLS) if d is None else p.fg("err", "◆", bold=True) + p.dim("·" * (AXIS_COLS - 1))
        return head + mark + " " + p.fg("err", rpad(short_status(s) if d is None else f"{d} d", 5))
    end = round(d / 2)
    cells = [(grad_role(d - i * 2, ctx.threshold), "╍" if d - i * 2 <= ctx.threshold else "━")
             for i in range(min(end, AXIS_COLS - 1))]
    if end >= AXIS_COLS:
        cells.append(("g1", "›"))
    else:
        cells.append((lvl, "◆"))
        cells += [("dim", "·")] * (AXIS_COLS - 1 - end)
    return head + _runs(cells, p) + " " + rpad(f"{d} d", 5)


def _strip(p: Painter, states, ctx: Context) -> str:
    marks: Dict[int, str] = {}
    for s in states:
        if s.days_left is not None:
            marks[min(AXIS_COLS - 1, max(0, round(s.days_left / 2)))] = level(s, ctx.threshold)
    runs = set(_run_cols(ctx))
    cells = [(marks[i], "◆") if i in marks else ("dim", "┬" if i in runs else "─") for i in range(AXIS_COLS)]
    return p.dim(t("today") + " ") + _runs(cells, p) + p.dim(" +90 d")


def _axis_labels(p: Painter, ctx: Context) -> List[str]:
    lead = " " * (2 + NAME_W)
    cols = _run_cols(ctx)
    out = []
    if cols:
        out.append(lead + p.fg("accent", "".join("▴" if i in cols else " " for i in range(AXIS_COLS)).rstrip()))
    labels = [" "] * AXIS_COLS

    def put(col: int, text: str) -> None:
        if col + len(text) <= AXIS_COLS and all(c == " " for c in labels[max(0, col - 1):col + len(text) + 1]):
            labels[col:col + len(text)] = list(text)

    put(0, t("today"))
    put(AXIS_COLS - 5, "+90 d")
    month = (ctx.now.replace(day=1) + timedelta(days=32)).replace(day=1)
    while (month - ctx.now).days < AXIS_DAYS:
        put(round((month - ctx.now).days / 2), f"1.{month.month}")
        month = (month + timedelta(days=32)).replace(day=1)
    return out + [lead + p.dim("".join(labels).rstrip())]


def timeline_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = [_lane(p, s, ctx) for s in states] + _axis_labels(p, ctx)
    out.append(p.dim("━ " + t("valid") + "   ╍ " + t("renewal window ({n} days)", n=ctx.threshold)
                     + "   ◆ " + t("expiry") + ("   ▴ " + t("timer run") if ctx.next_run else "")))
    plan = plan_runs(states, ctx)
    if plan:
        out += ["", p.bold(t("upcoming runs"))]
        for when, group in plan:
            items = [(s.name, p.fg(level(s, ctx.threshold), s.name)) for s in group]
            lines = wrap_join(items, INNER - 14, 14)
            out.append("  " + p.fg("accent", pad(day(when), 10)) + "  " + lines[0])
            out += lines[1:]
    else:
        out += ["", p.dim(t("timer not active, nothing will renew on its own"))]
    return "\n".join(out)


def timeline_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    cols = 40
    scale = max(elapsed, 1.0) / cols
    out = [p.dim(t("run took {elapsed}, 1 char = {scale}", elapsed=duration(elapsed), scale=duration(scale)))]
    for (label, ok), (start, length) in zip(results, timings):
        lead = min(cols - 1, int(start / scale))
        bar = "█" * max(1, min(cols - lead, round(length / scale))) if length >= 1 else "·"
        out.append("  " + pad(clip(label, 24), 25) + " " * lead + p.fg("ok" if ok else "err", bar)
                   + " " * max(1, cols - lead - len(bar) + 1) + (p.fg("ok", t("done")) if ok else p.fg("err", t("failed"))))
    ok = sum(1 for _, s in results if s)
    out += ["", p.fg("ok", t("done {n}", n=ok)) + "  " + (p.fg("err", t("failed {n}", n=len(results) - ok))
                                                          if ok < len(results) else p.dim(t("failed {n}", n=0)))]
    return "\n".join(out + _failures(p, results))


def timeline_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    out = [row(p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version), p.dim(run_label(ctx))),
           _strip(p, states, ctx)] + run_alert(p, ctx) + [""]
    # Two columns; left and right arrows jump between them (see nav_step).
    cells = [f"{_mark(p, i == sel)} {p.fg('accent', key, bold=True)} {p.bold(label) if i == sel else label}"
             for i, (key, label, _) in enumerate(items)]
    half = (len(cells) + 1) // 2
    for i in range(half):
        out.append(pad(cells[i], 40) + (cells[i + half] if i + half < len(cells) else ""))
    return "\n".join(out + ["", _hint(p)])


def timeline_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    head = p.fg("accent", "xCert", bold=True) + "  " + _strip(p, states, ctx)
    if not bad:
        return head + " " + p.fg("ok", f"{len(states)} OK")
    return "\n".join([head] + [f"       {p.fg('err', '○' if not s.present else '◆')} {s.name}  {status_text(s)}"
                               for s in bad])


# ======================
# CONSOLE
# ======================
def _bar(p: Painter, segments: Sequence[Optional[Tuple[str, str, bool]]]) -> str:
    """Full-width bar; a None segment stretches to fill the remaining space."""
    fixed = sum(len(s[1]) for s in segments if s)
    fill = max(0, WIDTH - fixed)
    return "".join(p.chip(s[0], s[1], s[2]) if s else p.chip("bar", " " * fill) for s in segments)


def _title(p: Painter, ctx: Context, states, sub: str) -> str:
    segs: List[Optional[Tuple[str, str, bool]]] = [("bar", f" xCert {ctx.version} ", True), ("bar", f" {sub} ", False), None]
    for n, label, lvl in counts(states, ctx.threshold):
        segs.append((lvl, f" {n} {t(label)} ", False))
    segs.append(("bar", f" {clip(ctx.host, 16)} ", False))
    return _bar(p, segs)


def _tag(p: Painter, role: str, text: str) -> str:
    inner = p.dim(text) if role == "dim" else p.fg(role, text, bold=True)
    return p.dim("[") + inner + p.dim("]")


TAGS = {"ok": "  OK  ", "due": "RENEW ", "critical": "URGENT", "expired": "EXPIRD", "missing": " NONE ",
        "unreadable": " READ "}


def _meter(p: Painter, s: CertState, ctx: Context, inner: int) -> str:
    label = short_status(s)
    width = inner - len(label) - 1
    bar = smooth_bar(s.days_left / AXIS_DAYS, width) if s.days_left and s.days_left > 0 else ""
    lvl = level(s, ctx.threshold)
    painted_label = p.bold(label) if lvl == "ok" else p.fg(lvl, label, bold=True)
    return (p.dim("[") + p.fg(grad_role(s.days_left, ctx.threshold), bar) + " " * (width - len(bar) + 1)
            + painted_label + p.dim("]"))


def _fkeys(p: Painter, items, sel: Optional[int] = None) -> str:
    widths = [WIDTH // len(items)] * len(items)
    widths[-1] += WIDTH - sum(widths)  # the last key takes the remainder, so the bar spans the screen
    return "".join(p.fg("accent", key, bold=True)
                   + p.chip("bar", pad(("▸" if i == sel else " ") + short, cw - len(key)), accent=i == sel)
                   for i, ((key, _, short), cw) in enumerate(zip(items, widths)))


def console_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    head = "  " + pad(t("state"), 9) + pad(t("certificate"), 27) + pad(t("provider"), 13) + pad(t("expires"), 11)
    out = [_title(p, ctx, states, t("certificates")), p.chip("bar", pad(head + t("validity"), WIDTH))]
    for s in states:
        cat = category(s, ctx.threshold)
        out.append(" " + _tag(p, level(s, ctx.threshold), t(TAGS[cat])) + " " + pad(clip(s.name, 26), 27)
                   + p.dim(pad(clip(s.provider, 12), 13)) + pad(f"{s.expires:%d.%m.%Y}" if s.expires else "-", 11)
                   + _meter(p, s, ctx, 16))
        out.extend(" " * 10 + p.dim("+ " + san) for san in s.sans)
    out.append(_bar(p, [("bar", " " + t("threshold {n} days", n=ctx.threshold) + " ", False), None,
                        ("bar", f" {run_label(ctx)} ", False)]))
    return "\n".join(out)


def console_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    ok = sum(1 for _, s in results if s)
    segs: List[Optional[Tuple[str, str, bool]]] = [("ok", " " + t("renewed {n}", n=ok) + " ", False)]
    if ok < len(results):
        segs.append(("err", " " + t("failed {n}", n=len(results) - ok) + " ", False))
    segs += [None, ("bar", f" {duration(elapsed)} ", False)]
    # Per-certificate lines are already on screen (live run) or in the log above.
    return "\n".join([_bar(p, segs)] + _failures(p, results, "  "))


def console_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    meters = [f"  {pad(clip(s.name, 25), 26)}{_meter(p, s, ctx, WIDTH - 38)}" for s in states]
    tail = ["", "  " + p.dim(t("timer")) + "  " + (p.fg("ok", t("active")) + ", " + run_label(ctx) if ctx.next_run
                                                   else p.fg("err", t("not active")))] + run_alert(p, ctx, "  ")
    tail += ["", _hint(p), _fkeys(p, items, sel)]
    return "\n".join([_title(p, ctx, states, "menu"), ""] + _window(p, meters, None, _room(2 + len(tail))) + tail)


def console_motd(p: Painter, ctx: Context, states) -> str:
    out = [_title(p, ctx, states, t("{n} certificates", n=len(states)))]
    for s in states:
        if level(s, ctx.threshold) == "err":
            out.append(_tag(p, "err", t(TAGS[category(s, ctx.threshold)])) + f" {s.name}, {status_text(s)}")
    return "\n".join(out)


# ======================
# DISPATCH
# ======================
RENDERERS = {
    "ledger": {"list": ledger_list, "summary": ledger_summary, "menu": ledger_menu, "motd": ledger_motd},
    "timeline": {"list": timeline_list, "summary": timeline_summary, "menu": timeline_menu, "motd": timeline_motd},
    "console": {"list": console_list, "summary": console_summary, "menu": console_menu, "motd": console_motd},
}


def render_list(p: Painter, ctx: Context, states) -> str:
    return "\n".join([RENDERERS[p.theme]["list"](p, ctx, states)] + run_alert(p, ctx))


def render_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    return RENDERERS[p.theme]["summary"](p, ctx, results, timings, elapsed)


def render_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    items = [(key, t(label), t(short)) for key, label, short in items]
    return RENDERERS[p.theme]["menu"](p, ctx, states, items, sel)


def render_motd(p: Painter, ctx: Context, states) -> str:
    return "\n".join([RENDERERS[p.theme]["motd"](p, ctx, states)] + run_alert(p, ctx))


def render_head(p: Painter, ctx: Context, sub: str) -> str:
    return "\n".join(_head(p, ctx, sub))


def nav_step(theme: str, count: int) -> int:
    """How far left and right jump in the main menu: a whole column in the two-column timeline."""
    return (count + 1) // 2 if theme == "timeline" else 1


THEME_NAMES = {"ledger": "ledger", "timeline": "timeline", "console": "console"}
COLOR_NAMES = {"auto": "auto", "full": "full (24-bit)", "16": "16 colours", "none": "none"}


LANGUAGE_NAMES = {"en": "English", "pl": "polski"}  # each in its own language, so it is recognisable
PICKER_ROWS = ("1", "2", "3", "c", "b", "l")  # cursor rows of the look picker, in screen order


def render_picker(p: Painter, ctx: Context, appearance: Dict[str, str], effective_mode: str,
                  language: str = "en", sel: Optional[int] = None) -> str:
    """Look picker: each option is drawn with a sample of that look."""
    sample_state = CertState("example.pl", "ovh", present=True, days_left=79,
                             expires=ctx.now + timedelta(days=79))
    samples = {
        "ledger": p.fg("ok", "●") + " example.pl   " + p.dim("ovh") + "   " + p.fg("ok", t("{n} days", n=79), bold=True),
        "timeline": _runs([(grad_role(79 - i * 2, ctx.threshold), "╍" if 79 - i * 2 <= ctx.threshold else "━")
                           for i in range(20)] + [("ok", "◆"), ("dim", "···")], p) + " 79 d",
        "console": _meter(p, sample_state, ctx, 30),
    }
    out = _head(p, ctx, t("look")) + ["  " + p.dim(t("saved to config.json")), ""]
    for i, theme in enumerate(THEMES):
        current = theme == appearance["theme"]
        name = p.bold(pad(THEME_NAMES[theme], 10)) if current else pad(THEME_NAMES[theme], 10)
        out.append(f" {_mark(p, i == sel)}{p.fg('accent', str(i + 1), bold=True)}  "
                   f"{p.fg('accent', '✓') if current else ' '} {name} {samples[theme]}")
    note = "" if appearance["colors"] == effective_mode else p.dim(
        "  " + t("(now {mode})", mode=t(COLOR_NAMES[effective_mode])))
    settings = (("c", t("colours"), t(COLOR_NAMES[appearance["colors"]]), note),
                ("b", t("background"), t(appearance["background"]), ""),
                ("l", t("language"), LANGUAGE_NAMES[language], ""))
    out.append("")
    for i, (key, label, value, extra) in enumerate(settings, 3):
        out.append(f" {_mark(p, i == sel)}{p.fg('accent', key, bold=True)}  {pad(label, 13)}{p.bold(value)}{extra}")
    keys = [("↑↓", t("move")), ("←→", t("change")), ("Enter", t("save")), ("q", t("back"))]
    out += ["",
            p.dim("  " + t("one-off for a single command:") + " xcert --theme console --colors 16 --list"),
            p.dim("  " + t("NO_COLOR, journald and cron always get plain text")),
            "",
            _keys_line(p, keys)]
    return "\n".join(out)


def _keys_line(p: Painter, keys: Sequence[Tuple[str, str]]) -> str:
    if p.theme == "console":
        return _fkeys(p, [(key, "", label) for key, label in keys])
    return "  " + "   ".join(f"{p.fg('accent', k, bold=True)} {label}" for k, label in keys)


def render_editor(p: Painter, ctx: Context, config: Dict, path: str, sel: int, items, domain_items) -> str:
    """Configuration editor: domain rows first, then the editor's own actions, one cursor over both."""
    general = config.get("general", {})
    domains = config.get("domains", [])
    default = general.get("default_provider") or t("none")
    logs = p.fg("ok", t("enabled")) if general.get("logs", 1) == 1 else p.fg("err", t("disabled"))
    out = _head(p, ctx, t("configuration"))
    out += ["  " + p.dim(t("default provider")) + " " + p.bold(clip(default, 20)) + "   " + p.dim(t("logs")) + " " + logs,
            "  " + p.dim(t("after renewal")) + " " + clip(general.get("post_renewal_cmd") or t("none"), INNER - 20),
            "  " + p.dim(t("file") + " " + clip(path, INNER - 8)), ""]
    sep = p.dim("│") if p.theme == "timeline" else " "
    if p.theme == "console":
        out.append(p.chip("bar", pad("    " + pad(t("no."), 4) + pad(t("domain"), 28) + pad(t("provider"), 14)
                                     + t("email"), WIDTH)))
    rows = []
    for i, d in enumerate(domains):
        on = i == sel
        name = pad(clip(d.get("domain", ""), 27), 28)
        rows.append(f" {_mark(p, on)}{p.fg('accent', rpad(str(i + 1), 2))}{sep} " + (p.bold(name) if on else name)
                    + p.dim(pad(clip(d.get("provider") or default, 13), 14)) + p.dim(clip(d.get("email", ""), INNER - 48)))
    if not domains:
        rows.append("  " + p.dim(t("No domains configured.")))
    hint = "  " + p.dim(t("on a domain:")) + "  " + "   ".join(
        f"{p.fg('accent', key, bold=True)} {t(short)}" for key, _, short in [("Enter", "", "more")] + list(domain_items))
    if p.theme == "console":
        items = [(key, t(label), t(short)) for key, label, short in items]
        tail = ["", hint, _hint(p), _fkeys(p, items, sel - len(domains))]
    else:
        items = [(key, t(label), short) for key, label, short in items]
        tail = [""] + _action_rows(p, items, sel, len(domains)) + ["", hint, _hint(p)]
    focus = sel if sel < len(domains) else len(domains) - 1
    return "\n".join(out + _window(p, rows, focus, _room(len(out) + len(tail))) + tail)


def render_choice(p: Painter, ctx: Context, title: str, options: Sequence[str], sel: int,
                  current: Optional[str] = None) -> str:
    """Short pick list (providers, actions on a domain) in the current look."""
    out = _head(p, ctx, title) + [""]
    rows = []
    for i, option in enumerate(options):
        on = i == sel
        text = clip(option, INNER - 28)
        rows.append(f" {_mark(p, on)}{p.fg('accent', rpad(str(i + 1), 2), bold=True)}  " + (p.bold(text) if on else text)
                    + (p.dim("  " + t("(current)")) if option == current else ""))
    tail = ["", _hint(p, back=True)]
    return "\n".join(out + _window(p, rows, sel, _room(len(out) + len(tail))) + tail)


def render_report(p: Painter, ctx: Context, states: Sequence[CertState], path: str) -> str:
    """Confirmation after the validity report is written, with what is closest to expiring."""
    known = [s for s in states if s.days_left is not None and s.days_left >= 0]
    nearest = min(known, key=lambda s: s.days_left, default=None)
    bad = [s for s in states if category(s, ctx.threshold) in ("expired", "missing", "unreadable")]
    saved = t("report saved")
    lines = []
    if nearest:
        lines.append("  " + t("closest to expiry: {name}, {status}", name=nearest.name,
                              status=p.fg(level(nearest, ctx.threshold), status_text(nearest))))
    if bad:
        items = [(t(category(s, 10 ** 6)) + " " + s.name, p.fg("err", t(category(s, 10 ** 6))) + " " + s.name)
                 for s in bad]
        lines += ["  " + line for line in wrap_join(items, INNER - 2, 2)]
    if p.theme == "console":
        segs: List[Optional[Tuple[str, str, bool]]] = [(lvl, f" {n} {t(label)} ", False)
                                                        for n, label, lvl in counts(states, ctx.threshold)]
        head = " " + _tag(p, "ok", t("  OK  ")) + " " + saved + ": " + clip(path, INNER - len(saved) - 12)
        return "\n".join(_head(p, ctx, t("Report")) + [head, _bar(p, segs + [None])] + lines)
    head = p.fg("ok", "✓") + " " + saved + "  " + p.dim(clip(path, INNER - len(saved) - 4))
    return "\n".join(([_strip(p, states, ctx)] if p.theme == "timeline" else []) + [head] + lines)


NOTE_JUNK_RE = re.compile(r"[←-⯿️\U0001F000-\U0001FFFF]+\s*")


def note_text(message: str) -> str:
    """A log message fit for a screen row: emoji dropped, since terminals disagree on their width."""
    return NOTE_JUNK_RE.sub("", message).strip()


CHECK_MARKS = {"ok": "✓", "warn": "!", "err": "✗"}


def render_check(p: Painter, ctx: Context, checks: Sequence[Tuple[str, str, str]], path: str) -> str:
    """Outcome of --validate-config: one row per section, provider or setting, then the totals."""
    errors = sum(1 for lvl, _, _ in checks if lvl == "err")
    warnings = sum(1 for lvl, _, _ in checks if lvl == "warn")
    tags = {"ok": t("  OK  "), "warn": t(" WARN "), "err": t(" FAIL ")}
    if p.theme == "console":
        out = [_bar(p, [("bar", f" xCert {ctx.version} ", True), ("bar", f" {t('checking')} ", False), None,
                        ("bar", f" {clip(path, 40)} ", False)])]
        for lvl, label, detail in checks:
            line = " " + _tag(p, lvl, tags[lvl]) + " " + label
            if detail:
                line += ": " + (p.dim if lvl == "ok" else lambda s: p.fg(lvl, s))(
                    clip(note_text(detail), WIDTH - 12 - len(label)))
            out.append(line)
        segs: List[Optional[Tuple[str, str, bool]]] = [
            ("err" if errors else "ok", " " + t("errors {n}", n=errors) + " ", False),
            ("warn" if warnings else "bar", " " + t("warnings {n}", n=warnings) + " ", False), None]
        return "\n".join(out + [_bar(p, segs)])

    if p.theme == "timeline":
        out = _head(p, ctx, t("checking")) + ["", p.fg("accent", "config.json", bold=True) + " " + p.dim(clip(path, 60))]
        lead = [p.dim("└─" if i == len(checks) - 1 else "├─") for i in range(len(checks))]
    else:
        out = _head(p, ctx, t("checking"))
        lead = [" "] * len(checks)
    width = min(38, max((len(label) for _, label, _ in checks), default=0))
    for (lvl, label, detail), branch in zip(checks, lead):
        text = clip(note_text(detail), INNER - 9 - width)
        line = f" {branch} {p.fg(lvl, CHECK_MARKS[lvl])}  {pad(clip(label, width), width)} "
        out.append((line + (p.dim(text) if lvl == "ok" else p.fg(lvl, text))).rstrip())
    if errors or warnings:
        total = (p.fg("err", t("errors {n}", n=errors)) if errors else p.dim(t("errors {n}", n=0))) + "   " \
            + (p.fg("warn", t("warnings {n}", n=warnings)) if warnings else p.dim(t("warnings {n}", n=0)))
    else:
        total = p.fg("ok", t("all checks passed"))
    return "\n".join(out + ([_ledger_rule(p)] if p.theme == "ledger" else [""]) + [total])


def render_step(p: Painter, name: str, outcome: str, provider: str = "", seconds: float = 0,
                notes: Sequence[str] = (), days: Optional[int] = None) -> str:
    """One certificate of a renewal run: `renew`/`issue` while certbot works, then `skip`, `ok` or `fail`."""
    if outcome == "skip":
        what = t("skipped, valid for {n} more days", n=days)
    elif outcome in ("renew", "issue"):
        what = t("renewing via {provider} ...", provider=provider) if outcome == "renew" \
            else t("issuing via {provider} ...", provider=provider)
    elif outcome == "ok":
        what = t("done via {provider}", provider=provider)
    else:
        what = t("failed")
    notes = [clip(note_text(n), INNER - 30) for n in notes[-2:]]
    if p.theme == "console":
        tag = {"skip": ("dim", "  --  "), "renew": ("info", "  ..  "), "issue": ("info", "  ..  "),
               "ok": ("ok", t("  OK  ")), "fail": ("err", t(" FAIL "))}[outcome]
        line = " " + _tag(p, *tag) + " " + clip(name, 24) + ": " + (p.dim(what) if outcome == "skip" else what)
        if outcome == "ok":
            line += " " + p.dim(f"({duration(seconds)})")
        return "\n".join([line] + [" " * 10 + (p.fg("err", n) if outcome == "fail" else p.dim(n)) for n in notes])
    head = "  " + pad(clip(name, 27), 28)
    if outcome == "skip":
        line = head + p.dim(what)
    elif outcome == "ok":
        line = head + what + "  " + p.fg("ok", t("done"), bold=True) + " " + p.dim(duration(seconds))
    elif outcome == "fail":
        line = head + p.fg("err", what, bold=True)
    else:
        line = head + what
    return "\n".join([line] + [" " * 30 + (p.fg("err", n) if outcome == "fail" else p.dim(n)) for n in notes])


# ======================
# KEYBOARD AND SCREEN
# ======================
KEY_NAMES = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[C": "right", b"\x1b[D": "left",
             b"\x1bOA": "up", b"\x1bOB": "down", b"\x1bOC": "right", b"\x1bOD": "left",
             b"\x1b": "esc", b"\r": "enter", b"\n": "enter", b"\x04": "q", b"": "q"}


def decode_key(data: bytes) -> str:
    """Raw bytes of one key press to a name ('up', 'enter', 'esc') or the lowercased character."""
    if data == b"\x03":
        raise KeyboardInterrupt
    if data in KEY_NAMES:
        return KEY_NAMES[data]
    if data.startswith(b"\x1b"):
        return KEY_NAMES.get(data[:3], "")  # other escape sequences (F-keys, Ctrl+arrow) do nothing
    return data.decode("utf-8", "ignore").lower()


def interactive() -> bool:
    """Single keys and in-place redraw need a real terminal with termios (Linux, macOS)."""
    try:
        import termios  # noqa: F401
    except ImportError:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


class _Resized(Exception):
    pass


def _on_resize(signum, frame):
    raise _Resized


def set_screen(cols: int, rows: int = 0) -> None:
    global WIDTH, INNER, ROWS, TERM_COLS
    WIDTH = max(MIN_WIDTH, min(MAX_WIDTH, cols))
    INNER = WIDTH - 2
    ROWS = rows
    TERM_COLS = cols if cols < MIN_WIDTH else 0


def fit_screen() -> None:
    """Sizes the next frame to the terminal window (PuTTY, tmux, a resized xterm)."""
    if interactive():
        size = shutil.get_terminal_size()
        set_screen(size.columns, size.lines)


def read_key(prompt_text: str = "") -> str:
    """One key press, no Enter needed; 'resize' when the window changed size meanwhile.

    Off a terminal (pipes, tests) it reads a whole line instead.
    """
    global _pending
    if not interactive():
        return input(prompt_text).strip().lower() or "enter"
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    if not _pending:
        import select
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        previous = signal.signal(signal.SIGWINCH, _on_resize)
        try:
            tty.setraw(fd)
            _pending = os.read(fd, 64)
            # An arrow is ESC [ A; a lone Esc has nothing behind it, so wait a moment for the rest.
            if _pending == b"\x1b" and select.select([fd], [], [], 0.03)[0]:
                _pending += os.read(fd, 64)
        except _Resized:
            _pending = b""
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            signal.signal(signal.SIGWINCH, previous)
        fit_screen()
        if not _pending:
            return "resize"
    # A held-down arrow delivers several keys in one read; hand them out one at a time.
    data, _pending = split_key(_pending)
    return decode_key(data)


_pending = b""


def split_key(buf: bytes) -> Tuple[bytes, bytes]:
    """First key press in `buf` and the rest: an escape sequence, a UTF-8 character or a single byte."""
    if buf[:1] == b"\x1b" and buf[1:2] in (b"[", b"O"):
        end = 2
        while end < len(buf) and not 0x40 <= buf[end] <= 0x7E:  # CSI parameters run up to a final byte
            end += 1
        return buf[:end + 1], buf[end + 1:]
    if buf[0] >= 0xC0:
        size = 2 if buf[0] < 0xE0 else 3 if buf[0] < 0xF0 else 4
        return buf[:size], buf[size:]
    return buf[:1], buf[1:]


def cut(text: str, width: int) -> str:
    """Cuts a painted line to `width` visible columns, keeping its colour codes intact."""
    out, seen = [], 0
    for piece in re.split(r"(\x1b\[[0-9;]*m)", text):
        if piece.startswith("\x1b"):
            out.append(piece)
        elif seen < width:
            out.append(piece[:width - seen])
            seen += len(out[-1])
    return "".join(out) + ("\033[0m" if "\x1b" in text else "")


def clear() -> None:
    """Clears the terminal and hides the cursor: these screens are driven by keys, there is nothing to type."""
    if interactive():
        sys.stdout.write("\033[?25l\033[H\033[J")


def show_cursor() -> None:
    if interactive():
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def draw(text: str) -> None:
    """Redraws the screen in place on a terminal, so menus do not pile up; appends below otherwise."""
    if not interactive():
        print("\n" + text)
        return
    if TERM_COLS:  # narrower than the layouts: cut instead of letting every line wrap
        text = "\n".join(cut(line, TERM_COLS) for line in text.split("\n"))
    clear()
    print(text)


def pause(p: Painter) -> None:
    """Keeps a result on screen until a key is pressed, before the menu redraws over it."""
    if interactive():
        sys.stdout.write("\n" + p.dim(t("press any key to go back")))
        while read_key() == "resize":
            pass


def move(key: str, index: int, count: int, step: int = 1) -> int:
    """Cursor after an arrow key; wraps around, any other key leaves it put."""
    delta = {"up": -1, "down": 1, "left": -step, "right": step}.get(key)
    return index if delta is None or not count else (index + delta) % count
