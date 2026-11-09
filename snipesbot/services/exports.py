"""On-demand exports. The .xlsx is a *view* of the database now, not a second
copy kept in sync row by row."""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook

from ..env import Env
from . import people

HEADER = ["Date", "Time", "Sniper", "Sniped", "Group Snipe", "Notable", "Kind", "Scores", "Permalink", "ID"]


def _scores(e, dns: set) -> str:
    """Whether a snipe earns points: not if nobody was sniped, nor for do-not-snipe targets."""
    if e.kind.value != "snipe":
        return ""
    if not e.sniped:
        return "No (nobody sniped yet)"
    protected = [u for u in e.sniped if u in dns]
    if len(protected) == len(e.sniped):
        return "No (do-not-snipe)"
    return "Partly (do-not-snipe target excluded)" if protected else "Yes"


def build_xlsx(env: Env) -> bytes:
    names = env.store.names.all()
    dns = set(env.store.dns.list())
    tz = env.cfg.tz
    wb = Workbook()
    ws = wb.active
    ws.title = "Snipes"
    ws.append(HEADER)
    for e in env.store.entries.all_active():
        t = e.local_time(tz)
        ws.append([
            t.strftime("%Y-%m-%d"), t.strftime("%H:%M:%S"),
            people.display(e.sniper, names), ", ".join(people.display(u, names) for u in e.sniped),
            "Yes" if e.group_snipe else "No", "Yes" if e.notable else "No", e.kind.value,
            _scores(e, dns), env.permalink(e.channel, e.message_ts), e.id,
        ])
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_xlsx(env: Env, path) -> int:
    data = build_xlsx(env)
    with open(path, "wb") as f:
        f.write(data)
    return env.store.entries.count()
