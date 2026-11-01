"""Terminal rendering for xCert.

Three looks share one data model: ledger (quiet table), timeline (90-day axis)
and console (htop-like bars). Colours come from per-look 24-bit palettes, fall
back to the 16 ANSI colours, or disappear for journald, cron and NO_COLOR.
Every screen fits in 80 columns and avoids emoji, whose width terminals disagree on.
"""
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

THEMES = ("ledger", "timeline", "console")
COLOR_MODES = ("auto", "full", "16", "none")
BACKGROUNDS = ("dark", "light")
DEFAULT_APPEARANCE = {"theme": "ledger", "colors": "auto", "background": "dark"}

WIDTH = 80
INNER = WIDTH - 2
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
        return "no certificate"
    if cat == "unreadable":
        return "certificate unreadable"
    if cat == "expired":
        return f"expired {-s.days_left} days ago"
    return f"{s.days_left} days left"


def short_status(s: CertState) -> str:
    cat = category(s, 10 ** 6)
    return {"missing": "none", "unreadable": "n/a", "expired": "expired"}.get(cat, f"{s.days_left} d")


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


def row(left: str, right: str, width: int = INNER) -> str:
    return left + " " * max(1, width - visible_len(left) - visible_len(right)) + right


def day(dt: datetime) -> str:
    return f"{WEEKDAYS[dt.weekday()]} {dt.day}.{dt.month}"


def run_label(ctx: Context) -> str:
    return f"next run {day(ctx.next_run)} {ctx.next_run:%H:%M}" if ctx.next_run else "timer not active"


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
        parts.append(p.fg(lvl, dot) + f" {n} {label}")
    return sep.join(parts) if parts else p.dim("no certificates configured")


def _failures(p: Painter, results: Sequence[Tuple[str, bool]], indent: str = "") -> List[str]:
    failed = [label for label, ok in results if not ok]
    if not failed:
        return []
    return ["", indent + "failed: " + ", ".join(p.fg("err", f) for f in failed),
            indent + "check " + p.fg("accent", "xcert --validate-config", bold=True)
            + " and the error log, then run " + p.fg("accent", "xcert --auto", bold=True)]


# ======================
# LEDGER
# ======================
def _ledger_rule(p: Painter) -> str:
    return p.dim("─" * INNER)


def _ledger_header(p: Painter, ctx: Context, sub: str = "") -> List[str]:
    left = p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version) + ("   " + p.bold(sub) if sub else "")
    return [row(left, p.dim(f"{ctx.host}  {ctx.now:%d.%m %H:%M}")), _ledger_rule(p)]


def ledger_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = _ledger_header(p, ctx, "certificates")
    out.append("  " + p.dim(pad("certificate", 26) + pad("provider", 13) + pad("expires", 11)
                            + rpad("days", 4) + "  after renewal"))
    for s in states:
        lvl = level(s, ctx.threshold)
        if s.expires is None:
            mid = p.fg("err", pad(status_text(s), 15))
        else:
            mid = pad(f"{s.expires:%d.%m.%Y}", 11) + p.fg(lvl, rpad(str(s.days_left), 4), bold=True)
        out.append(p.fg(lvl, "○" if not s.present else "●") + " " + pad(clip(s.name, 25), 26)
                   + p.dim(pad(clip(s.provider, 12), 13)) + mid + "  " + p.dim(clip(s.reload, INNER - 58)))
        out.extend("  " + p.dim("+ " + san) for san in s.sans)
    out += [_ledger_rule(p), _counts_line(p, states, ctx, ", "),
            p.dim(f"renewal threshold {ctx.threshold} days, {run_label(ctx)}")]
    return "\n".join(out)


def ledger_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    ok = sum(1 for _, s in results if s)
    left = p.fg("ok", f"renewed {ok}") + "   " + (p.fg("err", f"failed {len(results) - ok}") if ok < len(results)
                                                    else p.dim("failed 0"))
    return "\n".join([_ledger_rule(p), row(left, p.dim(duration(elapsed)))] + _failures(p, results))


def ledger_menu(p: Painter, ctx: Context, states, items) -> str:
    out = _ledger_header(p, ctx)
    out += ["  " + _counts_line(p, states, ctx), "  " + p.dim(run_label(ctx)), ""]
    out += [f"  {p.fg('accent', key, bold=True)}   {label}" for key, label, _ in items]
    return "\n".join(out + [""])


def ledger_motd(p: Painter, ctx: Context, states) -> str:
    bad = [s for s in states if level(s, ctx.threshold) == "err"]
    head = p.fg("accent", "xCert", bold=True) + "  "
    if not bad:
        return row(head + p.fg("ok", f"{len(states)} certificates OK"), p.dim(run_label(ctx)))
    out = [row(head + f"{len(states)} certificates, " + p.fg("err", f"{len(bad)} need attention"),
               p.dim(run_label(ctx)))]
    out += [f"  {p.fg('err', '○' if not s.present else '●')} {pad(clip(s.name, 25), 26)} {status_text(s)}" for s in bad]
    return "\n".join(out + ["  " + p.dim("details: xcert --list")])


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
    return p.dim("today ") + _runs(cells, p) + p.dim(" +90 d")


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

    put(0, "today")
    put(AXIS_COLS - 5, "+90 d")
    month = (ctx.now.replace(day=1) + timedelta(days=32)).replace(day=1)
    while (month - ctx.now).days < AXIS_DAYS:
        put(round((month - ctx.now).days / 2), f"1.{month.month}")
        month = (month + timedelta(days=32)).replace(day=1)
    return out + [lead + p.dim("".join(labels).rstrip())]


def timeline_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = [_lane(p, s, ctx) for s in states] + _axis_labels(p, ctx)
    out.append(p.dim(f"━ valid   ╍ renewal window ({ctx.threshold} days)   ◆ expiry"
                     + ("   ▴ timer run" if ctx.next_run else "")))
    plan = plan_runs(states, ctx)
    if plan:
        out += ["", p.bold("upcoming runs")]
        for when, group in plan:
            items = [(s.name, p.fg(level(s, ctx.threshold), s.name)) for s in group]
            lines = wrap_join(items, INNER - 14, 14)
            out.append("  " + p.fg("accent", pad(day(when), 10)) + "  " + lines[0])
            out += lines[1:]
    else:
        out += ["", p.dim("timer not active, nothing will renew on its own")]
    return "\n".join(out)


def timeline_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    cols = 40
    scale = max(elapsed, 1.0) / cols
    out = [p.dim(f"run took {duration(elapsed)}, 1 char = {duration(scale)}")]
    for (label, ok), (start, length) in zip(results, timings):
        lead = min(cols - 1, int(start / scale))
        bar = "█" * max(1, min(cols - lead, round(length / scale))) if length >= 1 else "·"
        out.append("  " + pad(clip(label, 24), 25) + " " * lead + p.fg("ok" if ok else "err", bar)
                   + " " * max(1, cols - lead - len(bar) + 1) + (p.fg("ok", "done") if ok else p.fg("err", "failed")))
    ok = sum(1 for _, s in results if s)
    out += ["", p.fg("ok", f"done {ok}") + "  " + (p.fg("err", f"failed {len(results) - ok}")
                                                   if ok < len(results) else p.dim("failed 0"))]
    return "\n".join(out + _failures(p, results))


def timeline_menu(p: Painter, ctx: Context, states, items) -> str:
    out = [row(p.fg("accent", "xCert", bold=True) + "  " + p.dim(ctx.version), p.dim(run_label(ctx))),
           _strip(p, states, ctx), ""]
    half = (len(items) + 1) // 2
    for left, right in zip(items[:half], items[half:] + [None] * (2 * half - len(items))):
        cell = f"  {p.fg('accent', left[0], bold=True)} {left[1]}"
        out.append(pad(cell, 40) + (f"{p.fg('accent', right[0], bold=True)} {right[1]}" if right else ""))
    return "\n".join(out + [""])


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
        segs.append((lvl, f" {n} {label} ", False))
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


def _fkeys(p: Painter, items) -> str:
    cw = WIDTH // len(items)
    return "".join(p.fg("accent", key, bold=True) + p.chip("bar", pad(" " + short, cw - len(key))) for key, _, short in items)


def console_list(p: Painter, ctx: Context, states: Sequence[CertState]) -> str:
    out = [_title(p, ctx, states, "certificates"),
           p.chip("bar", pad("  state    certificate                provider     expires    validity", WIDTH))]
    for s in states:
        cat = category(s, ctx.threshold)
        out.append(" " + _tag(p, level(s, ctx.threshold), TAGS[cat]) + " " + pad(clip(s.name, 26), 27)
                   + p.dim(pad(clip(s.provider, 12), 13)) + pad(f"{s.expires:%d.%m.%Y}" if s.expires else "-", 11)
                   + _meter(p, s, ctx, 16))
        out.extend(" " * 10 + p.dim("+ " + san) for san in s.sans)
    out.append(_bar(p, [("bar", f" threshold {ctx.threshold} days ", False), None, ("bar", f" {run_label(ctx)} ", False)]))
    return "\n".join(out)


def console_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    ok = sum(1 for _, s in results if s)
    segs: List[Optional[Tuple[str, str, bool]]] = [("ok", f" renewed {ok} ", False)]
    if ok < len(results):
        segs.append(("err", f" failed {len(results) - ok} ", False))
    segs += [None, ("bar", f" {duration(elapsed)} ", False)]
    lines = [" " + _tag(p, "ok" if s else "err", "  OK  " if s else " FAIL ") + " " + label for label, s in results]
    return "\n".join(lines + [_bar(p, segs)] + _failures(p, results, "  "))


def console_menu(p: Painter, ctx: Context, states, items) -> str:
    out = [_title(p, ctx, states, "menu"), ""]
    out += [f"  {pad(clip(s.name, 25), 26)}{_meter(p, s, ctx, 42)}" for s in states]
    out += ["", "  " + p.dim("timer") + "  " + (p.fg("ok", "active") + ", " + run_label(ctx) if ctx.next_run
                                                else p.fg("err", "not active")), "", _fkeys(p, items)]
    return "\n".join(out)


def console_motd(p: Painter, ctx: Context, states) -> str:
    out = [_title(p, ctx, states, f"{len(states)} certificates")]
    for s in states:
        if level(s, ctx.threshold) == "err":
            out.append(_tag(p, "err", TAGS[category(s, ctx.threshold)]) + f" {s.name}, {status_text(s)}")
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
    return RENDERERS[p.theme]["list"](p, ctx, states)


def render_summary(p: Painter, ctx: Context, results, timings, elapsed: float) -> str:
    return RENDERERS[p.theme]["summary"](p, ctx, results, timings, elapsed)


def render_menu(p: Painter, ctx: Context, states, items) -> str:
    return RENDERERS[p.theme]["menu"](p, ctx, states, items)


def render_motd(p: Painter, ctx: Context, states) -> str:
    return RENDERERS[p.theme]["motd"](p, ctx, states)


def prompt(p: Painter) -> str:
    return p.fg("accent", "choose ›" if p.theme == "console" else "›") + " "


THEME_NAMES = {"ledger": "ledger", "timeline": "timeline", "console": "console"}
COLOR_NAMES = {"auto": "auto", "full": "full (24-bit)", "16": "16 colours", "none": "none"}


def render_picker(p: Painter, ctx: Context, appearance: Dict[str, str], effective_mode: str) -> str:
    """Look picker: each option is drawn with a sample of that look."""
    sample_state = CertState("example.pl", "ovh", present=True, days_left=79,
                             expires=ctx.now + timedelta(days=79))
    samples = {
        "ledger": p.fg("ok", "●") + " example.pl   " + p.dim("ovh") + "   " + p.fg("ok", "79 days", bold=True),
        "timeline": _runs([(grad_role(79 - i * 2, ctx.threshold), "╍" if 79 - i * 2 <= ctx.threshold else "━")
                           for i in range(20)] + [("ok", "◆"), ("dim", "···")], p) + " 79 d",
        "console": _meter(p, sample_state, ctx, 30),
    }
    out = [row(p.fg("accent", "xCert", bold=True) + "  " + p.bold("look"), p.dim("saved to config.json")), ""]
    for i, theme in enumerate(THEMES, 1):
        current = theme == appearance["theme"]
        marker = p.fg("accent", "▸") if current else " "
        out.append(f"  {marker} {p.fg('accent', str(i), bold=True)}  {pad(THEME_NAMES[theme], 10)} {samples[theme]}")
    note = "" if appearance["colors"] == effective_mode else p.dim(f"  (now {COLOR_NAMES[effective_mode]})")
    out += ["",
            f"  {p.fg('accent', 'c', bold=True)}  colours      {p.bold(COLOR_NAMES[appearance['colors']])}{note}",
            f"  {p.fg('accent', 'b', bold=True)}  background   {p.bold(appearance['background'])}",
            "",
            p.dim("  one-off for a single command: xcert --theme console --colors 16 --list"),
            p.dim("  NO_COLOR, journald and cron always get plain text"),
            "",
            f"  {p.fg('accent', '1-3', bold=True)} look   {p.fg('accent', 'c', bold=True)} colours   "
            f"{p.fg('accent', 'b', bold=True)} background   {p.fg('accent', 's', bold=True)} save   "
            f"{p.fg('accent', 'q', bold=True)} back"]
    return "\n".join(out)
