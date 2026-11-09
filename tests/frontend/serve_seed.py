"""Seeded server for the frontend smoke test (fake Slack, temp data dir)."""
import pathlib, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from conftest import FakeSlack, photo_msg, text_msg, ALICE, BOB, CAROL, ZEKE, CHANNEL
from snipesbot.config import Config
from snipesbot.env import Env
from snipesbot.services import archiver, entries as svc
from snipesbot.models import Kind
from snipesbot.web import create_app

tmp = tempfile.mkdtemp()
cfg = Config(bot_token="x", channel_id=CHANNEL, zeke_user_id=ZEKE, data_dir=pathlib.Path(tmp), timezone_name="America/New_York")
slack = FakeSlack()
slack.users[BOB] = "<script>window.__pwned=1</script>Bob"          # hostile display name
env = Env.create(cfg, slack=slack)
import struct, zlib

def portrait_png(w=720, h=1280):
    """A phone-shaped gradient photo: the hardest case for fitting a photo on one screen."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes(((x * 255 // w), (y * 255 // h), 160) [i % 3] for x in range(w) for i in range(3)) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))

PHOTO = portrait_png()
archiver._download = lambda e, f: e.store.archive.save_file(f["id"], f.get("name",""), f.get("mimetype",""), PHOTO) or True

def add(i, caption, user=CAROL, **kw):
    msg = photo_msg(f"{1788000000 + i*100}.000100", user, caption)
    msg["files"][0]["name"] = "<img src=x onerror=window.__pwned=1>.jpg" if i == 1 else msg["files"][0]["name"]
    archiver.archive_message(env, msg)
    return svc.record_photo(env, msg)[0]

add(1, "<img src=x onerror=window.__pwned=1> hostile caption")
add(2, f"<@{ALICE}> snipe")
add(3, f"<@{BOB}>")                                                  # likely_snipe (pending)
add(4, "who is this")                                                # unidentified (pending)
sp = add(5, "a deer?"); svc.apply_state(env, sp.id, Kind.SPECIAL, sniped=())
env.store.archive.save_message(text_msg("1788000401.000100", ALICE, "<img src=x onerror=window.__pwned=1> context"))
inc = add(6, "marked green in Slack, nobody tagged")
svc.apply_state(env, inc.id, Kind.SNIPE, allow_incomplete=True)           # a "malformed snipe": snipe, nobody sniped
for u in (ALICE, BOB, CAROL):
    from snipesbot.services import people; people.ensure_name(env, u)
import time
from snipesbot.services import maintenance
maintenance.TASKS["slow_demo"] = maintenance.Task(
    "slow_demo", "Slow demo", "Takes a few seconds so a running task is observable.",
    lambda e, p, progress: [progress(f"working… step {i}") or time.sleep(1) for i in range(1, 4)])
app = create_app(env)
app.run(port=int(sys.argv[1]), threaded=True)
