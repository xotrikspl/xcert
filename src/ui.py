"""Terminal rendering for xCert.

Seven looks share one data model: ledger (quiet table), timeline (90-day axis),
console (htop-like bars), calendar (month grids), board (departures board),
tree (grouped by provider) and cards (framed certificates). Colours come from
per-look 24-bit palettes, fall
back to the 16 ANSI colours, or disappear for journald, cron and NO_COLOR.
Every screen fits in 80 columns and avoids emoji, whose width terminals disagree on.
"""
import calendar
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

THEMES = ("ledger", "timeline", "console", "calendar", "board", "tree", "cards")
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
    "calendar": {  # rose on graphite
        "dark": {
            "fg": "#e2dde3", "accent": "#f28cb1", "ok": "#8fd4a4", "warn": "#e9c46a", "err": "#f2848a",
            "info": "#9cc3e6", "grad": ("#8fd4a4", "#bcd68c", "#e9c46a", "#eea36f", "#f2848a"),
            "bar": "#2c2530", "okbg": "#1f3a2b", "warnbg": "#40351b", "errbg": "#47232a",
        },
        "light": {
            "fg": "#2a2430", "accent": "#b8336a", "ok": "#2e7d4f", "warn": "#8c6400", "err": "#b5303f",
            "info": "#2b6592", "grad": ("#2e7d4f", "#5f7f22", "#8c6400", "#ad5521", "#b5303f"),
            "bar": "#f1e6ee", "okbg": "#d9eedf", "warnbg": "#f4e7c3", "errbg": "#f6d6da",
        },
    },
    "board": {  # amber
        "dark": {
            "fg": "#f1e4c6", "accent": "#ffb020", "ok": "#a8d672", "warn": "#ffcf5a", "err": "#ff7a6b",
            "info": "#8cc8e8", "grad": ("#a8d672", "#d2d866", "#ffcf5a", "#ffa24d", "#ff7a6b"),
            "bar": "#2a241a", "okbg": "#263a1c", "warnbg": "#43361a", "errbg": "#47201b",
        },
        "light": {
            "fg": "#221d12", "accent": "#9a5c00", "ok": "#3e7a1f", "warn": "#8a6300", "err": "#b2321f",
            "info": "#23668f", "grad": ("#3e7a1f", "#6a7a14", "#8a6300", "#a8501a", "#b2321f"),
            "bar": "#efe4c8", "okbg": "#dcebd0", "warnbg": "#f3e3bb", "errbg": "#f6d5cd",
        },
    },
    "tree": {  # teal
        "dark": {
            "fg": "#d6e2e0", "accent": "#5ec8c0", "ok": "#93d69a", "warn": "#e3c66d", "err": "#ef8a8a",
            "info": "#8bbbe8", "grad": ("#93d69a", "#bdd68a", "#e3c66d", "#eba06e", "#ef8a8a"),
            "bar": "#1d2a2a", "okbg": "#1e3826", "warnbg": "#3b341b", "errbg": "#42252a",
        },
        "light": {
            "fg": "#1d2a29", "accent": "#13787a", "ok": "#2b7a3c", "warn": "#876500", "err": "#b3343c",
            "info": "#275f93", "grad": ("#2b7a3c", "#5c7a1f", "#876500", "#a8531c", "#b3343c"),
            "bar": "#e2eeed", "okbg": "#d6eedb", "warnbg": "#f2e6c2", "errbg": "#f5d6d8",
        },
    },
    "cards": {  # violet
        "dark": {
            "fg": "#e0dcef", "accent": "#c3a6ff", "ok": "#8edcb0", "warn": "#ecd17c", "err": "#f59aa7",
            "info": "#94c2f2", "grad": ("#8edcb0", "#b6da8c", "#ecd17c", "#f0ab78", "#f59aa7"),
            "bar": "#282440", "okbg": "#1f3d32", "warnbg": "#433a1d", "errbg": "#4a2430",
        },
        "light": {
            "fg": "#241f38", "accent": "#6a3fd0", "ok": "#1f7a50", "warn": "#8a6800", "err": "#b52f48",
            "info": "#2d62a8", "grad": ("#1f7a50", "#527d1c", "#8a6800", "#aa531b", "#b52f48"),
            "bar": "#ebe6fb", "okbg": "#d4eee1", "warnbg": "#f3e8c4", "errbg": "#f8d6dd",
        },
    },
}
ANSI_FG = {"fg": "39", "ok": "32", "warn": "33", "err": "31", "info": "34",
           "g1": "32", "g2": "32", "g3": "33", "g4": "33", "g5": "31"}
ANSI_ACCENT = {"ledger": "39", "timeline": "36", "console": "36", "calendar": "95", "board": "33", "tree": "36",
               "cards": "35"}
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

    def reverse(self, text: str) -> str:
        return self._sgr("7;1", text)

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
    if p.theme == "board":
        return _tile_keys(p, [("↑↓", t("move")), ("Enter", t("select"))] + ([("q", t("back"))] if back else []))
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


def _action_rows(p: Painter, items, sel: Optional[int] = None, first: int = 0, bold_sel: bool = True) -> List[str]:
    """One `key  label` row per item; `first` is the cursor index of the first row."""
    return [f" {_mark(p, first + i == sel)}{p.fg('accent', key, bold=True)}   "
            + (p.bold(label) if bold_sel and first + i == sel else label) for i, (key, label, _) in enumerate(items)]


def _rows(p: Painter, items, sel: Optional[int] = None, first: int = 0, bold_sel: bool = True,
          last_row: bool = True) -> List[str]:
    """Pickable rows in the current look: tiles on the board, branches in the tree, plain keys elsewhere."""
    if p.theme == "board":
        return _tile_rows(p, items, sel, first, bold_sel)
    if p.theme == "tree":
        return _tree_rows(p, items, sel, first, last_row, bold_sel)
    return _action_rows(p, items, sel, first, bold_sel)


def _tree_head(p: Painter, ctx: Context, sub: str, lvl: str) -> str:
    """systemctl-status style title: the dot carries the overall state."""
    return row(p.fg(lvl, "●") + " " + p.fg("accent", "xCert", bold=True) + " " + p.dim(ctx.version)
               + (" - " + p.bold(sub) if sub else ""), p.dim(f"{ctx.host}  {ctx.now:%d.%m %H:%M}"))


def _head(p: Painter, ctx: Context, sub: str) -> List[str]:
    """Screen title in the current look; long titles (a domain name) are cut to leave room for the run time."""
    sub = clip(sub, 28)
    if p.theme == "ledger":
        return _ledger_header(p, ctx, sub)
    if p.theme == "timeline":
        return [row(p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version) + "   " + p.bold(sub),
                    p.dim(run_label(ctx)))]
    if p.theme == "calendar":
        return [row(_brand(p, ctx, sub), p.dim(f"{day(ctx.now)}.{ctx.now.year}  {ctx.now:%H:%M}"))]
    if p.theme == "board":
        return [_board_bar(p, f"xCert {ctx.version}" + (f"   {sub}" if sub else ""),
                           f"{clip(ctx.host, 16)}   {day(ctx.now)} {ctx.now:%H:%M}")]
    if p.theme == "tree":
        return [_tree_head(p, ctx, sub, _worst(ctx.states, ctx.threshold))]
    if p.theme == "cards":
        return [row(_brand(p, ctx, sub), p.dim(f"{ctx.host}  {ctx.now:%d.%m %H:%M}"))]
    return [_title(p, ctx, ctx.states, sub)]


# ======================
# LEDGER# ======================
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


def tally(results, skipped) -> Tuple[int, int, int]:
    """Renewed, skipped (still valid) and failed certificates of a run."""
    failed = sum(1 for _, ok in results if not ok)
    skips = sum(1 for label, ok in results if ok and label in skipped)
    return len(results) - failed - skips, skips, failed


def _totals(p: Painter, results, skipped, sep: str) -> str:
    renewed, skips, failed = tally(results, skipped)
    text = t("done {n}", n=renewed) if p.theme == "timeline" else t("renewed {n}", n=renewed)
    parts = [p.fg("ok", text) if renewed else p.dim(text)]
    if skips:
        parts.append(p.dim(t("skipped {n}", n=skips)))
    parts.append(p.fg("err", t("failed {n}", n=failed)) if failed else p.dim(t("failed {n}", n=0)))
    return sep.join(parts)


def ledger_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    left = _totals(p, results, skipped, "   ")
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


def timeline_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    cols = 40
    scale = max(elapsed, 1.0) / cols
    out = [p.dim(t("run took {elapsed}, 1 char = {scale}", elapsed=duration(elapsed), scale=duration(scale)))]
    for (label, ok), (start, length) in zip(results, timings):
        lead = min(cols - 1, int(start / scale))
        bar = "█" * max(1, min(cols - lead, round(length / scale))) if length >= 1 else "·"
        out.append("  " + pad(clip(label, 24), 25) + " " * lead + p.fg("ok" if ok else "err", bar)
                   + " " * max(1, cols - lead - len(bar) + 1)
                   + (p.dim(t("skipped")) if ok and label in skipped else p.fg("ok", t("done")) if ok
                      else p.fg("err", t("failed"))))
    out += ["", _totals(p, results, skipped, "  ")]
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


def console_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    renewed, skips, failed = tally(results, skipped)
    segs: List[Optional[Tuple[str, str, bool]]] = [("ok" if renewed else "bar", " " + t("renewed {n}", n=renewed) + " ",
                                                    False)]
    if skips:
        segs.append(("bar", " " + t("skipped {n}", n=skips) + " ", False))
    if failed:
        segs.append(("err", " " + t("failed {n}", n=failed) + " ", False))
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
# SHARED BY THE NEWER LOOKS
# ======================
def _wide(base: int) -> int:
    """A name column of `base` at 80 columns that takes every extra column of a wider window."""
    return base + WIDTH - MIN_WIDTH


def _renew_dates(states: Sequence[CertState], ctx: Context) -> Dict[str, datetime]:
    """Certificate name -> the timer run that renews it (empty without an active timer)."""
    return {s.name: when for when, group in plan_runs(states, ctx, len(states)) for s in group}


def _by_expiry(s: CertState) -> Tuple[bool, int]:
    """Sort key: soonest expiry first, certificates without a date last."""
    return s.days_left is None, s.days_left or 0


def _worst(states: Sequence[CertState], threshold: int) -> str:
    levels = {level(s, threshold) for s in states}
    return next((lvl for lvl in ("err", "warn", "ok") if lvl in levels), "accent")


def _brand(p: Painter, ctx: Context, sub: str) -> str:
    return p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version) + ("   " + p.bold(sub) if sub else "")


def _dot(p: Painter, s: CertState, threshold: int) -> str:
    return p.fg(level(s, threshold), "○" if not s.present else "●")


# ======================
# CALENDAR
# ======================
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
GRID_W = 21  # seven days of two digits and a marker slot


def _cal_months(ctx: Context, count: int = 3) -> List[datetime]:
    """First days of the months to draw; a month with under a week left gives way to the next one."""
    first = ctx.now.replace(day=1)
    if calendar.monthrange(first.year, first.month)[1] - ctx.now.day < 7:
        first = (first + timedelta(days=32)).replace(day=1)
    months = [first]
    while len(months) < count:
        months.append((months[-1] + timedelta(days=32)).replace(day=1))
    return months


def _month_grid(p: Painter, month: datetime, states: Sequence[CertState], ctx: Context) -> List[str]:
    """One month as 8 rows of GRID_W columns. The slot after each day carries a marker in plain text,
    so expiry days (`*`) and today (`<`) survive NO_COLOR."""
    rank = ("ok", "warn", "err")
    expiry: Dict = {}
    for s in states:
        if s.expires:
            lvl = level(s, ctx.threshold)
            if rank.index(lvl) >= rank.index(expiry.get(s.expires.date(), "ok")):
                expiry[s.expires.date()] = lvl
    runs = {(ctx.next_run + timedelta(days=7 * k)).date() for k in range(15)} if ctx.next_run else set()
    title = f"{t(MONTHS[month.month - 1])} {month.year}"
    lines = [p.bold(" " * ((GRID_W - 1 - len(title)) // 2) + title), p.dim(t("Mo Tu We Th Fr Sa Su"))]
    plain = p.mode == "none"
    for week in calendar.Calendar().monthdatescalendar(month.year, month.month):
        cells = []
        for d in week:
            num = f"{d.day:2d}"
            if d.month != month.month:
                cells.append("   ")
            elif d in expiry:
                lvl = expiry[d]
                cells.append((p.chip(lvl, num) if p.mode == "full" else p.fg(lvl, num, bold=True)) + ("*" if plain else " "))
            elif d == ctx.now.date():
                cells.append(p.reverse(num) + ("<" if plain else " "))
            elif d in runs:
                cells.append(p.fg("accent", num, bold=True) + " ")
            else:
                cells.append((p.dim(num) if d < ctx.now.date() else num) + " ")
        lines.append("".join(cells))
    return [pad(line, GRID_W) for line in lines + [""] * (8 - len(lines))]


def _cal_legend(p: Painter) -> str:
    if p.mode == "none":
        return "  12* " + t("expiry day") + "   12< " + t("today")
    swatch = (lambda lvl, text: p.chip(lvl, f" {text} ") if p.mode == "full" else p.fg(lvl, text, bold=True))
    return ("  " + swatch("ok", t("ok")) + " " + swatch("warn", t("due")) + " " + swatch("err", t("expired")) + " "
            + p.dim(t("expiry day")) + "   " + p.fg("accent", "2", bold=True) + " " + p.dim(t("timer run"))
            + "   " + p.reverse("31") + " " + p.dim(t("today")))


def _cal_date(p: Painter, s: CertState) -> str:
    if s.expires is None:
        return p.dim(pad("-", 11))
    text = pad(f"{s.expires:%d.%m}", 6) + p.dim(pad(t(WEEKDAYS[s.expires.weekday()]), 5))
    return p.fg("err", text) if s.days_left is not None and s.days_left < 0 else text


def calendar_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = _head(p, ctx, t("certificates")) + [""]
    grids = [_month_grid(p, m, states, ctx) for m in _cal_months(ctx, (WIDTH - 2) // (GRID_W + 2))]
    out += [("  " + "  ".join(parts)).rstrip() for parts in zip(*grids)]
    out += [_cal_legend(p), ""]
    renew = _renew_dates(states, ctx)
    for s in sorted(states, key=_by_expiry):
        lvl = level(s, ctx.threshold)
        name = clip(s.name, _wide(26)) + (p.dim(f" +{len(s.sans)}") if s.sans else "")
        line = (f"  {_cal_date(p, s)} {_dot(p, s, ctx.threshold)} {pad(name, _wide(30))}"
                + p.fg(lvl, rpad(short_status(s), 8), bold=lvl != "ok"))
        if s.name in renew:
            line += "   " + p.dim(t("renew {when}", when=day(renew[s.name])))
        out.append(line)
    out += ["", "  " + _counts_line(p, states, ctx, ", "),
            "  " + p.dim(t("renewal threshold {n} days", n=ctx.threshold) + ", " + run_label(ctx))]
    return "\n".join(out)


def calendar_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    start = ctx.now - timedelta(seconds=elapsed)
    span = t("from {start} to {end}", start=f"{start:%H:%M}", end=f"{ctx.now:%H:%M}")
    return "\n".join(["", "  " + _totals(p, results, skipped, "   "),
                      "  " + p.dim(f"{day(ctx.now)}, {span}, {duration(elapsed)}"),
                      "  " + p.dim(run_label(ctx))] + _failures(p, results, "  "))


def calendar_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    out = _head(p, ctx, "") + [""]
    rows = _action_rows(p, items, sel)
    months = max(1, (WIDTH - 48) // (GRID_W + 2))  # the action labels keep about 46 columns
    grids = [_month_grid(p, m, states, ctx) for m in _cal_months(ctx, months)]
    right = ["  ".join(parts) for parts in zip(*grids)]
    for i in range(max(len(rows), len(right))):
        left = rows[i] if i < len(rows) else ""
        out.append((pad(left, WIDTH - months * (GRID_W + 2)) + (right[i] if i < len(right) else "")).rstrip())
    out += ["", "  " + _counts_line(p, states, ctx), "  " + p.dim(run_label(ctx))] + run_alert(p, ctx, "  ")
    return "\n".join(out + ["", _hint(p)])


def calendar_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    head = p.fg("accent", "xCert", bold=True) + "  "
    if not bad:
        out = [row(head + p.fg("ok", t("{n} certificates OK", n=len(states))), p.dim(run_label(ctx)))]
        dated = sorted((s for s in states if s.expires), key=_by_expiry)
        if dated:
            out.append("  " + p.dim(t("next expiry {when}: {name}", when=day(dated[0].expires), name=dated[0].name)))
        return "\n".join(out)
    out = [row(head + t("{n} certificates", n=len(states)) + ", " + p.fg("err", t("{n} need attention", n=len(bad))),
               p.dim(run_label(ctx)))]
    out += [f"  {_cal_date(p, s)} {_dot(p, s, ctx.threshold)} {pad(clip(s.name, _wide(25)), _wide(26))} {status_text(s)}"
            for s in sorted(bad, key=_by_expiry)]
    return "\n".join(out + ["  " + p.dim(t("details:") + " xcert --list")])


# ======================
# BOARD
# ======================
def _board_bar(p: Painter, left: str, right: str = "") -> str:
    left = clip(left, WIDTH - 4 - len(right))
    return p.chip("bar", " " + left + " " * max(1, WIDTH - 2 - len(left) - len(right)) + right + " ", accent=True)


def _zebra(p: Painter, groups: Sequence[List[str]]) -> List[str]:
    """Every other group of rows on the bar tint, like a departures board; only 24-bit has a tint
    subtle enough, the other modes keep plain rows."""
    if p.mode != "full":
        return [line for group in groups for line in group]
    bg = f"\033[48;2;{_rgb(p.pal['bar'])}m"
    return [bg + pad(line, WIDTH).replace("\033[0m", "\033[0m" + bg) + "\033[0m" if i % 2 else line
            for i, group in enumerate(groups) for line in group]


def _tile(p: Painter, key: str, on: bool = False) -> str:
    if p.mode == "none":
        return f"[{key}]"
    return p.reverse(p.fg("accent", f" {key} ", bold=True)) if on else p.chip("bar", f" {key} ", accent=True)


def _tile_rows(p: Painter, items, sel: Optional[int] = None, first: int = 0, bold_sel: bool = True) -> List[str]:
    return [f" {_mark(p, first + i == sel)}{_tile(p, key, first + i == sel)}  "
            + (p.bold(label) if bold_sel and first + i == sel else label) for i, (key, label, _) in enumerate(items)]


def _tile_keys(p: Painter, keys: Sequence[Tuple[str, str]]) -> str:
    return "  " + "   ".join(_tile(p, key) + " " + label for key, label in keys)


def _board_status(s: CertState, ctx: Context) -> Tuple[str, str]:
    cat = category(s, ctx.threshold)
    if cat == "ok":
        return "ok", t("scheduled")
    if cat == "due":
        return "warn", t("due")
    if cat == "critical":
        return "err", t("expiring")
    return "err", status_text(s) if cat == "missing" else t(cat)


def _board_row(p: Painter, s: CertState, ctx: Context, when: Optional[datetime]) -> str:
    if when is None:
        run = p.dim(pad("-", 10))
    else:
        run = pad(day(when), 10)
        run = p.fg("accent", run, bold=True) if when == ctx.next_run else p.dim(run)
    role, status = _board_status(s, ctx)
    return (" " + run + pad(clip(s.name, _wide(26)), _wide(27)) + p.dim(pad(clip(s.provider, 12), 13))
            + pad(f"{s.expires:%d.%m.%Y}" if s.expires else "-", 11) + p.fg(role, clip(status, 17), bold=role != "ok"))


def board_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    renew = _renew_dates(states, ctx)
    out = _head(p, ctx, t("certificates"))
    out.append(p.dim(" " + pad(t("run"), 10) + pad(t("certificate"), _wide(27)) + pad(t("provider"), 13) + pad(t("expires"), 11)
                     + t("status")))
    order = sorted(states, key=lambda s: (renew.get(s.name) or datetime.max, _by_expiry(s)))
    out += _zebra(p, [[_board_row(p, s, ctx, renew.get(s.name))] + [" " * 11 + p.dim("+ " + san) for san in s.sans]
                      for s in order])
    counted = ", ".join(f"{n} {t(label)}" for n, label, _ in counts(states, ctx.threshold))
    out.append(_board_bar(p, counted or t("no certificates configured"), run_label(ctx)))
    return "\n".join(out)


def board_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    out = [_board_bar(p, t("renewal"), duration(elapsed)),
           p.dim(" " + pad(t("start"), 8) + pad(t("certificate"), _wide(31)) + pad(t("result"), 14) + t("took"))]
    groups = []
    for (label, ok), (start, length) in zip(results, timings):
        start = int(start)
        result = (p.dim(t("skipped")) if ok and label in skipped else p.fg("ok", t("done")) if ok
                  else p.fg("err", t("failed"), bold=True))
        groups.append([" " + p.fg("accent", pad(f"+{start // 60}:{start % 60:02d}", 8)) + pad(clip(label, _wide(30)), _wide(31))
                       + pad(result, 14) + p.dim(duration(length) if length >= 1 else "-")])
    out += _zebra(p, groups) + ["", " " + _totals(p, results, skipped, "   ")]
    return "\n".join(out + _failures(p, results, " "))


def _names_line(names: Sequence[str], width: int) -> str:
    """Names joined by commas up to `width`, then how many did not fit."""
    shown: List[str] = []
    for name in names:
        if len(", ".join(shown + [name])) > width - 14:
            break
        shown.append(name)
    shown = shown or [clip(names[0], width - 14)]
    rest = len(names) - len(shown)
    return ", ".join(shown) + (", " + t("{n} more", n=rest) if rest else "")


def board_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    out = _head(p, ctx, "") + [""]
    plan = plan_runs(states, ctx, 1)
    if plan:
        when, group = plan[0]
        out += ["  " + t("next run {when}", when=p.fg("accent", f"{day(when)} {when:%H:%M}", bold=True))
                + "   " + p.dim(t("{n} certificates", n=len(group))),
                "    " + _names_line([s.name for s in group], INNER - 4)]
    elif not ctx.running:
        out.append("  " + p.fg("err", t("timer not active")))
    out += ["  " + _counts_line(p, states, ctx)] + run_alert(p, ctx, "  ") + [""]
    out += _tile_rows(p, items, sel)
    return "\n".join(out + ["", _hint(p)])


def board_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    if not bad:
        return _board_bar(p, "xCert   " + t("{n} certificates OK", n=len(states)), run_label(ctx))
    out = [_board_bar(p, "xCert   " + t("{n} certificates", n=len(states)) + ", " + t("{n} need attention", n=len(bad)),
                      run_label(ctx))]
    for s in bad:
        role, status = _board_status(s, ctx)
        out.append(" " + pad(clip(s.name, _wide(28)), _wide(29)) + p.dim(pad(f"{s.expires:%d.%m.%Y}" if s.expires else "-", 11))
                   + p.fg(role, status, bold=True))
    return "\n".join(out + [" " + p.dim(t("details:") + " xcert --list")])


# ======================
# TREE
# ======================
def _branch(p: Painter, last: bool) -> str:
    return p.dim("└─ " if last else "├─ ")


def _stem(p: Painter, last: bool) -> str:
    return p.dim("   " if last else "│  ")


def _tree_rows(p: Painter, items, sel: Optional[int] = None, first: int = 0, last_row: bool = True,
               bold_sel: bool = True) -> List[str]:
    """Actions as the branches of a tree; the cursor turns its branch into an arrow."""
    out = []
    for i, (key, label, _) in enumerate(items):
        on, last = first + i == sel, last_row and i == len(items) - 1
        lead = p.fg("accent", "└▸ " if last else "├▸ ") if on else _branch(p, last)
        out.append(" " + lead + p.fg("accent", key, bold=True) + "  " + (p.bold(label) if bold_sel and on else label))
    return out


def tree_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = _head(p, ctx, t("certificates")) + [""]
    groups: Dict[str, List[CertState]] = {}
    for s in states:
        groups.setdefault(s.provider, []).append(s)
    renew = _renew_dates(states, ctx)
    for provider, group in groups.items():
        out.append(p.fg("accent", provider, bold=True) + " " + p.dim(str(len(group))))
        for i, s in enumerate(group):
            last, lvl = i == len(group) - 1, level(s, ctx.threshold)
            if s.days_left is None:
                tail = p.fg("err", status_text(s))
            else:
                tail = p.fg(lvl, rpad(short_status(s), 7), bold=lvl != "ok") + "  " + p.dim(f"{s.expires:%d.%m.%Y}")
            if lvl != "ok" and s.name in renew:
                tail += "  " + p.fg(lvl, t("renew {when}", when=day(renew[s.name])))
            out.append(_branch(p, last) + _dot(p, s, ctx.threshold) + " " + pad(clip(s.name, _wide(33)), _wide(34)) + tail)
            out += [_stem(p, last) + _branch(p, j == len(s.sans) - 1) + p.dim(san) for j, san in enumerate(s.sans)]
    out += ["", _counts_line(p, states, ctx, ", "),
            p.dim(t("renewal threshold {n} days", n=ctx.threshold) + ", " + run_label(ctx))]
    return "\n".join(out)


def tree_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    failed = any(not ok for _, ok in results)
    out = ["", p.fg("err" if failed else "ok", "●") + " " + p.bold(t("renewal")) + "  " + p.dim(duration(elapsed))]
    for i, ((label, ok), (_, length)) in enumerate(zip(results, timings)):
        if ok and label in skipped:
            mark, result = p.dim("-"), p.dim(t("skipped"))
        elif ok:
            mark, result = p.fg("ok", "✓"), p.fg("ok", t("done"))
        else:
            mark, result = p.fg("err", "✗"), p.fg("err", t("failed"), bold=True)
        out.append(_branch(p, i == len(results) - 1) + mark + " " + pad(clip(label, _wide(30)), _wide(31)) + pad(result, 12)
                   + p.dim(duration(length) if length >= 1 else ""))
    return "\n".join(out + ["", _totals(p, results, skipped, "   ")] + _failures(p, results))


def tree_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    if ctx.running:
        last = t("renewal running now")
    elif ctx.last_run is None:
        last = p.dim(t("no run yet"))
    else:
        when = f"{day(ctx.last_run)} {ctx.last_run:%H:%M}"
        last = p.fg("ok", t("ok")) + p.dim(", " + when) if ctx.last_ok else p.fg("err", t("failed") + ", " + when)
    timer = (p.fg("ok", t("active")) + p.dim(", " + run_label(ctx))) if ctx.next_run else p.fg("err", t("not active"))
    facts = [(t("timer"), timer), (t("state"), _counts_line(p, states, ctx, ", ")), (t("last timer run"), last)]
    width = max(len(label) for label, _ in facts)
    out = _head(p, ctx, t("certificates")) + [f"  {rpad(label, width)}: {value}" for label, value in facts]
    out += run_alert(p, ctx, "  ") + ["", p.fg("accent", t("actions"), bold=True)] + _tree_rows(p, items, sel)
    return "\n".join(out + ["", _hint(p)])


def tree_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    head = p.fg("err" if bad else "ok", "●") + " " + p.fg("accent", "xCert", bold=True) + "  "
    if not bad:
        return row(head + t("{n} certificates OK", n=len(states)), p.dim(run_label(ctx)))
    out = [row(head + t("{n} certificates", n=len(states)) + ", " + p.fg("err", t("{n} need attention", n=len(bad))),
               p.dim(run_label(ctx)))]
    out += ["  " + _branch(p, i == len(bad) - 1) + _dot(p, s, ctx.threshold) + " " + pad(clip(s.name, _wide(28)), _wide(29))
            + status_text(s) for i, s in enumerate(bad)]
    return "\n".join(out + ["  " + p.dim(t("details:") + " xcert --list")])


# ======================
# CARDS
# ======================
CARD_MIN = 39  # two cards a row at 80 columns


def _per_row() -> int:
    return max(1, (WIDTH + 2) // (CARD_MIN + 2))


def _card_w() -> int:
    """Cards stretch so a row of them always spans the window."""
    return (WIDTH + 2) // _per_row() - 2


def _card(p: Painter, role: str, title: str, right: str, body: Sequence[str], width: int = 0) -> List[str]:
    """Rounded frame in a state colour, `title` and `right` set into the top border; `width` 0 spans the screen."""
    width = width or WIDTH
    frame = p.dim if role == "dim" else (lambda text: p.fg(role, text))
    room = width - 6 - (visible_len(right) + 2 if right else 0)
    title = title if visible_len(title) <= room else clip(ANSI_RE.sub("", title), room)
    fill = room - visible_len(title)
    top = (frame("╭─ ") + p.bold(title) + frame(" " + "─" * fill) + (" " + right + " " if right else "")
           + frame("─╮"))
    middle = [frame("│") + " " + pad(cut(line, width - 4) if visible_len(line) > width - 4 else line, width - 4)
              + " " + frame("│") for line in body]
    return [top] + middle + [frame("╰" + "─" * (width - 2) + "╯")]


def _card_grid(cards: Sequence[List[str]]) -> List[str]:
    """Cards side by side, as many as the screen width takes."""
    per, width = _per_row(), _card_w()
    out = []
    for i in range(0, len(cards), per):
        chunk = cards[i:i + per]
        out += ["  ".join(pad(card[n], width) for card in chunk).rstrip() for n in range(len(chunk[0]))]
    return out


def _cert_card(p: Painter, s: CertState, ctx: Context, renew: Dict[str, datetime]) -> List[str]:
    lvl = level(s, ctx.threshold)
    width = _card_w()
    meter_w = width - 4 - 11
    if s.days_left is None or s.days_left < 0:
        meter = p.dim("·" * meter_w)
    else:
        bar = smooth_bar(s.days_left / AXIS_DAYS, meter_w)
        meter = p.fg(grad_role(s.days_left, ctx.threshold), bar) + p.dim("·" * (meter_w - len(bar)))
    when = renew.get(s.name)
    nxt = p.fg("warn" if lvl != "ok" else "accent", t("renew {when}", when=f"{when.day}.{when.month}")) if when else ""
    return _card(p, lvl, clip(s.name, width - 12), p.dim(f"+{len(s.sans)}") if s.sans else "",
                 [p.fg(lvl, pad(short_status(s), 10), bold=True) + " " + meter,
                  p.dim(pad(clip(s.provider, 12), 13)) + pad(f"{s.expires:%d.%m.%Y}" if s.expires else "-", 11) + nxt],
                 width)


def cards_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    renew = _renew_dates(states, ctx)
    out = _head(p, ctx, t("certificates")) + [""]
    out += _card_grid([_cert_card(p, s, ctx, renew) for s in sorted(states, key=_by_expiry)])
    out += ["", "  " + _counts_line(p, states, ctx, ", "),
            "  " + p.dim(t("renewal threshold {n} days", n=ctx.threshold) + ", " + run_label(ctx))]
    return "\n".join(out)


def cards_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    body = []
    for (label, ok), (_, length) in zip(results, timings):
        if ok and label in skipped:
            body.append(p.dim("- " + pad(clip(label, _wide(34)), _wide(35)) + t("skipped")))
        else:
            body.append((p.fg("ok", "✓") if ok else p.fg("err", "✗")) + " " + pad(clip(label, _wide(34)), _wide(35))
                        + pad(p.fg("ok", t("done")) if ok else p.fg("err", t("failed"), bold=True), 12)
                        + p.dim(duration(length)))
    body += ["", _totals(p, results, skipped, "   ")]
    role = "err" if any(not ok for _, ok in results) else "ok"
    return "\n".join([""] + _card(p, role, t("renewal"), p.dim(duration(elapsed)), body) + _failures(p, results, "  "))


def cards_menu(p: Painter, ctx: Context, states, items, sel: Optional[int] = None) -> str:
    renew = _renew_dates(states, ctx)
    bad = sorted((s for s in states if level(s, ctx.threshold) != "ok"), key=_by_expiry)
    per = _per_row()
    out = _head(p, ctx, "") + [""]
    if bad:
        out += _card_grid([_cert_card(p, s, ctx, renew) for s in bad[:per]])
        if len(bad) > per:
            out.append("  " + p.dim(t("+{n} more, l lists all", n=len(bad) - per)))
    else:
        out.append("  " + p.fg("ok", t("{n} certificates OK", n=len(states))))
    out += run_alert(p, ctx, "  ")
    out += _card(p, "dim", t("actions"), p.dim(run_label(ctx)), _action_rows(p, items, sel))
    return "\n".join(out + [_hint(p)])


def cards_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    if not bad:
        return "\n".join(_card(p, "ok", "xCert", p.dim(run_label(ctx)),
                               [p.fg("ok", t("{n} certificates OK", n=len(states)))]))
    body = [f"{_dot(p, s, ctx.threshold)} {pad(clip(s.name, _wide(30)), _wide(31))} {status_text(s)}" for s in bad]
    body.append(p.dim(t("details:") + " xcert --list"))
    title = t("{n} certificates", n=len(states)) + ", " + p.fg("err", t("{n} need attention", n=len(bad)))
    return "\n".join(_card(p, "err", "xCert", title, body))


# ======================
# DISPATCH
# ======================
RENDERERS = {
    "ledger": {"list": ledger_list, "summary": ledger_summary, "menu": ledger_menu, "motd": ledger_motd},
    "timeline": {"list": timeline_list, "summary": timeline_summary, "menu": timeline_menu, "motd": timeline_motd},
    "console": {"list": console_list, "summary": console_summary, "menu": console_menu, "motd": console_motd},
    "calendar": {"list": calendar_list, "summary": calendar_summary, "menu": calendar_menu, "motd": calendar_motd},
    "board": {"list": board_list, "summary": board_summary, "menu": board_menu, "motd": board_motd},
    "tree": {"list": tree_list, "summary": tree_summary, "menu": tree_menu, "motd": tree_motd},
    "cards": {"list": cards_list, "summary": cards_summary, "menu": cards_menu, "motd": cards_motd},
}


def render_list(p: Painter, ctx: Context, states) -> str:
    return "\n".join([RENDERERS[p.theme]["list"](p, ctx, states)] + run_alert(p, ctx))


def render_summary(p: Painter, ctx: Context, results, timings, elapsed: float, skipped=()) -> str:
    return RENDERERS[p.theme]["summary"](p, ctx, results, timings, elapsed, set(skipped))


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


COLOR_NAMES = {"auto": "auto", "full": "full (24-bit)", "16": "16 colours", "none": "none"}


LANGUAGE_NAMES = {"en": "English", "pl": "polski"}  # each in its own language, so it is recognisable
# Cursor rows of the look picker, in screen order: one digit per look, then the settings.
PICKER_ROWS = tuple(str(i + 1) for i in range(len(THEMES))) + ("c", "b", "l")


def _samples(p: Painter, ctx: Context) -> Dict[str, str]:
    """One line per look for the picker, drawn the way that look draws a certificate 79 days from expiry."""
    state = CertState("example.pl", "ovh", present=True, days_left=79, expires=ctx.now + timedelta(days=79))
    renew = ctx.now + timedelta(days=49)
    expiry = p.chip("ok", "18") if p.mode == "full" else p.fg("ok", "18", bold=True)
    return {
        "ledger": p.fg("ok", "●") + " example.pl   " + p.dim("ovh") + "   " + p.fg("ok", t("{n} days", n=79), bold=True),
        "timeline": _runs([(grad_role(79 - i * 2, ctx.threshold), "╍" if 79 - i * 2 <= ctx.threshold else "━")
                           for i in range(20)] + [("ok", "◆"), ("dim", "···")], p) + " 79 d",
        "console": _meter(p, state, ctx, 30),
        "calendar": p.dim("15 16 17 ") + expiry + ("*" if p.mode == "none" else " ") + p.dim("19 20  ") + "example.pl",
        "board": p.fg("accent", pad(day(renew), 10), bold=True) + "example.pl   " + p.fg("ok", t("scheduled")),
        "tree": p.fg("accent", "ovh", bold=True) + " " + p.dim("└─ ") + p.fg("ok", "●") + " example.pl  "
        + p.fg("ok", "79 d", bold=True),
        "cards": p.fg("ok", "╭─ ") + p.bold("example.pl") + p.fg("ok", " ──── ") + p.fg("ok", "79 d", bold=True)
        + p.fg("ok", " ─╮"),
    }


def render_picker(p: Painter, ctx: Context, appearance: Dict[str, str], effective_mode: str,
                  language: str = "en", sel: Optional[int] = None) -> str:
    """Look picker: each option is drawn with a sample of that look."""
    samples = _samples(p, ctx)
    looks = [(str(i + 1), (p.fg("accent", "✓") if theme == appearance["theme"] else " ") + " "
              + (p.bold(pad(theme, 10)) if theme == appearance["theme"] else pad(theme, 10)) + " " + samples[theme], "")
             for i, theme in enumerate(THEMES)]
    note = "" if appearance["colors"] == effective_mode else p.dim(
        "  " + t("(now {mode})", mode=t(COLOR_NAMES[effective_mode])))
    settings = [("c", pad(t("colours"), 13) + p.bold(t(COLOR_NAMES[appearance["colors"]])) + note, ""),
                ("b", pad(t("background"), 13) + p.bold(t(appearance["background"])), ""),
                ("l", pad(t("language"), 13) + p.bold(LANGUAGE_NAMES[language]), "")]
    out = _head(p, ctx, t("look")) + ["  " + p.dim(t("saved to config.json")), ""]
    if p.theme == "cards":
        out += _card(p, "dim", t("look"), "", _action_rows(p, looks, sel, bold_sel=False))
    else:
        out += _rows(p, looks, sel, bold_sel=False, last_row=False)
    out += ([] if p.theme == "tree" else [""]) + _rows(p, settings, sel, len(THEMES), bold_sel=False)
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
    if p.theme == "board":
        return _tile_keys(p, keys)
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
            "  " + p.dim(t("on failure")) + " " + (clip(general["notify_cmd"], INNER - 20) if general.get("notify_cmd")
                                                  else p.fg("warn", t("no notice"))),
            "  " + p.dim(t("file") + " " + clip(path, INNER - 8)), ""]
    columns = "    " + pad(t("no."), 4) + pad(t("domain"), 28) + pad(t("provider"), 14) + t("email")
    if p.theme == "console":
        out.append(p.chip("bar", pad(columns, WIDTH)))
    elif p.theme == "board":
        out.append(p.dim(columns[2:]))
    elif p.theme == "tree":
        out.append(p.fg("accent", t("domains and emails ({n})", n=len(domains)), bold=True))
    states = {s.name: s for s in ctx.states}
    rows = []
    for i, d in enumerate(domains):
        on = i == sel
        name = pad(clip(d.get("domain", ""), 27), 28)
        lead = f" {_mark(p, on)}{p.fg('accent', rpad(str(i + 1), 2))}"
        if p.theme == "timeline":
            lead += p.dim("│") + " "
        elif p.theme == "tree":
            lead += " " + _branch(p, i == len(domains) - 1)
        else:
            lead += "  "
        if p.theme == "calendar":  # expiry date in place of the provider: this look thinks in dates
            s = states.get(d.get("domain", ""))
            when = (p.fg(level(s, ctx.threshold), pad(f"{s.expires:%d.%m.%Y}", 12)) if s and s.expires
                    else p.dim(pad("-", 12)))
            tail = when + p.dim(clip(d.get("email", ""), INNER - 46))
        else:
            tail = p.dim(pad(clip(d.get("provider") or default, 13), 14)) + p.dim(
                clip(d.get("email", ""), INNER - visible_len(lead) - 42 - (4 if p.theme == "cards" else 0)))
        rows.append(lead + (p.bold(name) if on else name) + tail)
    if not domains:
        rows.append("  " + p.dim(t("No domains configured.")))
    if p.theme == "board":
        rows = _zebra(p, [[line] for line in rows])
    hint = "  " + p.dim(t("on a domain:")) + "  " + "   ".join(
        f"{p.fg('accent', key, bold=True)} {t(short)}" for key, _, short in [("Enter", "", "more")] + list(domain_items))
    if p.theme == "console":
        items = [(key, t(label), t(short)) for key, label, short in items]
        tail = ["", hint, _hint(p), _fkeys(p, items, sel - len(domains))]
    else:
        items = [(key, t(label), short) for key, label, short in items]
        actions = ([p.fg("accent", t("actions"), bold=True)] if p.theme == "tree" else []) \
            + _rows(p, items, sel, len(domains))
        tail = [""] + actions + ["", hint, _hint(p)]
    focus = sel if sel < len(domains) else len(domains) - 1
    if p.theme == "cards":
        tail = _card(p, "dim", t("actions"), "", _action_rows(p, items, sel, len(domains))) + [hint, _hint(p)]
        window = _window(p, rows, focus, _room(len(out) + 2 + len(tail)))
        return "\n".join(out + _card(p, "dim", t("domains and emails ({n})", n=len(domains)), "", window) + tail)
    return "\n".join(out + _window(p, rows, focus, _room(len(out) + len(tail))) + tail)


def render_choice(p: Painter, ctx: Context, title: str, options: Sequence[str], sel: int,
                  current: Optional[str] = None) -> str:
    """Short pick list (providers, actions on a domain) in the current look."""
    out = _head(p, ctx, title) + [""]
    items = [(rpad(str(i + 1), 2), clip(option, INNER - 28) + (p.dim("  " + t("(current)")) if option == current else ""),
              "") for i, option in enumerate(options)]
    tail = ["", _hint(p, back=True)]
    if p.theme == "cards":
        rows = _window(p, _action_rows(p, items, sel), sel, _room(len(out) + 2 + len(tail)))
        return "\n".join(out + _card(p, "dim", title, "", rows) + tail)
    return "\n".join(out + _window(p, _rows(p, items, sel), sel, _room(len(out) + len(tail))) + tail)


def render_report(p: Painter, ctx: Context, states: Sequence[CertState], path: str) -> str:
    """Confirmation after the validity report is written, with what is closest to expiring."""
    known = [s for s in states if s.days_left is not None and s.days_left >= 0]
    nearest = min(known, key=lambda s: s.days_left, default=None)
    bad = [s for s in states if category(s, ctx.threshold) in ("expired", "missing", "unreadable")]
    saved = t("report saved")
    closest = t("closest to expiry: {name}, {status}", name=nearest.name,
                status=p.fg(level(nearest, ctx.threshold), status_text(nearest))) if nearest else ""
    problems = [(t(category(s, 10 ** 6)) + " " + s.name, p.fg("err", t(category(s, 10 ** 6))) + " " + s.name)
                for s in bad]
    if p.theme == "board":
        renew = _renew_dates(states, ctx)
        out = _head(p, ctx, t("Report")) + [" " + p.fg("ok", saved, bold=True) + "  " + p.dim(clip(path, INNER - len(saved) - 4))]
        out += ([" " + closest] if closest else []) + _zebra(p, [[_board_row(p, s, ctx, renew.get(s.name))] for s in bad])
        return "\n".join(out)
    if p.theme == "tree":
        leaves = ([closest] if closest else []) + [_dot(p, s, ctx.threshold) + " " + pad(clip(s.name, _wide(33)), _wide(34))
                                                   + p.fg("err", status_text(s)) for s in bad]
        out = [_tree_head(p, ctx, t("Report"), "ok"), "", p.fg("ok", saved, bold=True) + " " + p.dim(clip(path, INNER - len(saved) - 2))]
        return "\n".join(out + [_branch(p, i == len(leaves) - 1) + leaf for i, leaf in enumerate(leaves)])
    lines = []
    if closest:
        lines.append(("" if p.theme == "cards" else "  ") + closest)
    if bad:
        lines += [("" if p.theme == "cards" else "  ") + line
                  for line in wrap_join(problems, INNER - (6 if p.theme == "cards" else 2), 2)]
    if p.theme == "cards":
        return "\n".join(_head(p, ctx, t("Report")) + [""]
                         + _card(p, "ok", saved, "", [p.dim(clip(path, INNER - 4))] + ([""] + lines if lines else [])))
    if p.theme == "calendar":
        out = _head(p, ctx, t("Report")) + ["", "  " + p.fg("ok", "✓") + " " + saved + "  "
                                            + p.dim(clip(path, INNER - len(saved) - 6))] + lines
        if nearest:
            out += [""] + ["  " + line for line in _month_grid(p, nearest.expires.replace(day=1), states, ctx)]
        return "\n".join(line.rstrip() for line in out)
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


def render_check(p: Painter, ctx: Context, checks: Sequence[Tuple[str, str, str]], path: str,
                 title: str = "") -> str:
    """Outcome of --validate-config, --status or --install: one row per check, then the totals."""
    title = title or t("checking")
    errors = sum(1 for lvl, _, _ in checks if lvl == "err")
    warnings = sum(1 for lvl, _, _ in checks if lvl == "warn")
    worst = "err" if errors else "warn" if warnings else "ok"
    tags = {"ok": t("  OK  "), "warn": t(" WARN "), "err": t(" FAIL ")}
    if p.theme == "console":
        out = [_bar(p, [("bar", f" xCert {ctx.version} ", True), ("bar", f" {title} ", False), None,
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

    if p.theme == "board":
        out = _head(p, ctx, title)
        width = min(30, max((len(label) for _, label, _ in checks), default=0))
        groups = []
        for lvl, label, detail in checks:
            left = " " + pad(clip(label, width), width + 2) + (p.dim if lvl == "ok" else lambda s: p.fg(lvl, s))(
                clip(note_text(detail), INNER - width - 12))
            groups.append([row(left, p.fg(lvl, tags[lvl].strip(), bold=True), INNER)])
        totals = (t("errors {n}", n=errors) + "   " + t("warnings {n}", n=warnings)) if errors or warnings \
            else t("all checks passed")
        return "\n".join(out + _zebra(p, groups) + [_board_bar(p, totals, clip(path, WIDTH - len(totals) - 8))])

    if p.theme == "timeline":
        out = _head(p, ctx, title) + ["", p.fg("accent", "config.json", bold=True) + " " + p.dim(clip(path, 60))]
        lead = [p.dim("└─" if i == len(checks) - 1 else "├─") for i in range(len(checks))]
    elif p.theme == "tree":
        out = [_tree_head(p, ctx, title, worst), "", p.fg("accent", "config.json", bold=True) + " " + p.dim(clip(path, 60))]
        lead = [p.dim("└─" if i == len(checks) - 1 else "├─") for i in range(len(checks))]
    else:
        out = _head(p, ctx, title)
        lead = [" "] * len(checks)
    width = min(38, max((len(label) for _, label, _ in checks), default=0))
    rows = []
    for (lvl, label, detail), branch in zip(checks, lead):
        text = clip(note_text(detail), INNER - 9 - width - (4 if p.theme == "cards" else 0))
        line = f" {branch} {p.fg(lvl, CHECK_MARKS[lvl])}  {pad(clip(label, width), width)} "
        rows.append((line + (p.dim(text) if lvl == "ok" else p.fg(lvl, text))).rstrip())
    if errors or warnings:
        total = (p.fg("err", t("errors {n}", n=errors)) if errors else p.dim(t("errors {n}", n=0))) + "   " \
            + (p.fg("warn", t("warnings {n}", n=warnings)) if warnings else p.dim(t("warnings {n}", n=0)))
    else:
        total = p.fg("ok", t("all checks passed"))
    if p.theme == "cards":
        return "\n".join(out + [""] + _card(p, worst, "config.json", p.dim(clip(path, 40)), rows) + ["  " + total])
    return "\n".join(out + rows + ([_ledger_rule(p)] if p.theme == "ledger" else [""]) + [total])


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
    role = {"skip": "dim", "renew": "info", "issue": "info", "ok": "ok", "fail": "err"}[outcome]
    paint = (lambda text: p.dim(text) if role == "dim" else p.fg(role, text))
    note_lines = [p.fg("err", n) if outcome == "fail" else p.dim(n) for n in notes]
    if p.theme == "console":
        tag = {"skip": ("dim", "  --  "), "renew": ("info", "  ..  "), "issue": ("info", "  ..  "),
               "ok": ("ok", t("  OK  ")), "fail": ("err", t(" FAIL "))}[outcome]
        line = " " + _tag(p, *tag) + " " + clip(name, 24) + ": " + (p.dim(what) if outcome == "skip" else what)
        if outcome == "ok":
            line += " " + p.dim(f"({duration(seconds)})")
        return "\n".join([line] + [" " * 10 + n for n in note_lines])
    if p.theme == "calendar":  # wall-clock time on every row, so a slow certificate shows when it happened
        line = "  " + p.dim(f"{datetime.now():%H:%M:%S}") + "  " + pad(clip(name, 27), 28) + (
            paint(what) if outcome in ("skip", "fail") else what)
        if outcome == "ok":
            line += "  " + p.dim(duration(seconds))
        return "\n".join([line] + [" " * 14 + n for n in note_lines])
    if p.theme == "board":
        right = {"skip": "", "renew": p.fg("info", "..."), "issue": p.fg("info", "..."),
                 "ok": p.fg("ok", duration(seconds), bold=True), "fail": p.fg("err", t("failed"), bold=True)}[outcome]
        left = " " + pad(clip(name, 28), 29) + (p.dim(what) if outcome == "skip" else what if outcome != "fail" else "")
        return "\n".join([row(left, right, INNER)] + [" " * 30 + n for n in note_lines])
    if p.theme == "tree":
        mark = {"skip": "-", "renew": "○", "issue": "○", "ok": "✓", "fail": "✗"}[outcome]
        line = _branch(p, False) + paint(mark) + " " + pad(clip(name, 27), 28) + (
            paint(what) if outcome in ("skip", "fail") else what)
        if outcome == "ok":
            line += "  " + p.dim(duration(seconds))
        return "\n".join([line] + [_stem(p, False) + " " * 27 + n for n in note_lines])
    if p.theme == "cards":
        edge = paint("▌")
        line = edge + " " + pad(clip(name, 28), 29) + (paint(what) if outcome in ("skip", "fail") else what)
        if outcome == "ok":
            line += "  " + p.dim(duration(seconds))
        return "\n".join([line] + [edge + " " * 29 + n for n in note_lines])
    head = "  " + pad(clip(name, 27), 28)
    if outcome == "skip":
        line = head + p.dim(what)
    elif outcome == "ok":
        line = head + what + "  " + p.fg("ok", t("done"), bold=True) + " " + p.dim(duration(seconds))
    elif outcome == "fail":
        line = head + p.fg("err", what, bold=True)
    else:
        line = head + what
    return "\n".join([line] + [" " * 30 + n for n in note_lines])


# ======================
# KEYBOARD# ======================
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
