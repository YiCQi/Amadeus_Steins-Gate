"""Her notes (server/notes.py): status, migration, rollback, and checking recall.

    python tools/memory_notes.py status
    python tools/memory_notes.py migrate [--from DIR] [--review]   full backup first, then switch to notes
    python tools/memory_notes.py off | on                             back to the old fact list (facts.json), or notes again
    python tools/memory_notes.py restore BACKUP_DIR                   her memory as in that backup (what was said is kept)
    python tools/memory_notes.py check [--calibrate]                  recall tests, logged to recall_checks.jsonl
    python tools/memory_notes.py review                               have her look over her notes now (one request)

Run with the app's Python (%LOCALAPPDATA%\\Amadeus\\venv\\Scripts\\python.exe). --data DIR works on another
profile (a copy, for trying things out).

migrate --from DIR takes a store already prepared and reviewed (notes.json and the rest, as a dry run
leaves them); without it, the server learns the notes itself from past conversations, a few at a time.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("command", choices=["status", "migrate", "off", "on", "restore", "check", "review"])
p.add_argument("path", nargs="?", help="restore: the backup folder")
p.add_argument("--from", dest="source", help="migrate: a prepared store (a dry run's folder)")
p.add_argument("--review", action="store_true", help="migrate: have her look over the notes once (one request)")
p.add_argument("--calibrate", action="store_true", help="check: also suggest the recall thresholds")
p.add_argument("--data", help="another data folder (default: the app's data/)")
args = p.parse_args()
if args.data:
    os.environ["AMADEUS_DATA"] = str(Path(args.data).resolve())
sys.path.insert(0, str(ROOT))

from server import config, embed, memory, notes  # noqa: E402

STORE = ("notes.json", "changes.jsonl", "notes_state.json", "vectors.npz")
TESTS = notes.DIR / "recall_tests.json"
CHECKS = notes.DIR / "recall_checks.jsonl"
DEFAULT_TESTS = [   # what a question needs, as words a note about it would contain; [] = should recall nothing.
    # Examples only: your own go in data/memory/recall_tests.json (made from these on first use), which stays private.
    {"q": "我的工作最近怎么样", "expect": ["工作", "公司", "上班"]},
    {"q": "下周的考试准备得怎么样了", "expect": ["考试"]},
    {"q": "你还记得我养的猫吗", "expect": ["猫"]},
    {"q": "我室友又在宿舍做饭了", "expect": ["室友"]},
    {"q": "你还记得你父亲做了什么吗", "expect": ["父亲", "爸"]},
    {"q": "我们现在在哪条世界线上", "expect": ["世界线"]},
    {"q": "真由理是谁", "expect": ["真由理"]},
    {"q": "你知道你自己是什么吗", "expect": ["Amadeus", "记忆数据", "AI"]},
    {"q": "我们周末不是约好了吗", "expect": ["约好", "周六", "周末"]},
    {"q": "How is my job going?", "expect": ["工作", "job", "work"]},
    {"q": "今天天气不错", "expect": []},
    {"q": "你喜欢吃什么", "expect": []},
    {"q": "推荐一部电影吧", "expect": []},
    {"q": "我刚下班", "expect": []},
]


def say(*a):
    print(*a, flush=True)


def status():
    st, store = notes.state(), notes.load()
    by = lambda s: sum(1 for n in store if n.get("status") == s)  # noqa: E731
    active = [n for n in store if n.get("status") == "active"]
    say(f"notes: {'ON' if notes.enabled() else 'off (the old fact list is used)'}   ({notes.DIR})")
    say(f"  {len(active)} active: {sum(1 for n in active if n.get('tier') == 'core')} core "
        f"({len(notes.core_text(store))}/{notes.CORE_CHARS} characters), "
        f"{sum(1 for n in active if n.get('tier') != 'core')} archive; expired {by('expired')}, "
        f"retired {by('retired')}, forgotten {by('forgotten')}; {st.get('seq', 0)} changes logged")
    say(f"  conversations looked at for notes: up to #{st.get('replayed_upto', 0)} of #{memory.state()['consolidated']} sorted")
    say(f"  old fact list (facts.json): {len(memory.facts())} facts, untouched")
    say(f"  recall by meaning: {'yes' if embed.available() else 'no (words only)'}")
    backups = sorted((notes.DIR.parent / "memory-backups").glob("*")) if (notes.DIR.parent / "memory-backups").exists() else []
    say(f"  full backups: {len(backups)}" + (f", newest {backups[-1].name}" if backups else ""))


def summarizer():
    from server.llm import stream_chat
    settings = config.load_settings()

    async def summarize(system, text):
        out = ""
        async for chunk in stream_chat({**settings, "temperature": 0.3}, system, [{"role": "user", "content": text}]):
            out += chunk
        return out
    return summarize, settings


def migrate():
    if notes.enabled():
        say("already on notes: nothing to migrate")
        return
    dest = notes.backup("before-notes")
    if args.source:
        src = Path(args.source)
        for name in STORE:
            if (src / "memory" / name).exists() or (src / name).exists():
                f = src / "memory" / name if (src / "memory" / name).exists() else src / name
                shutil.copy2(f, notes.DIR / name)
        done = json.loads((src / "memory" / "state.json").read_text(encoding="utf-8")).get("consolidated", 0) \
            if (src / "memory" / "state.json").exists() else 0
        notes.set_state(enabled=True, replayed_upto=done, migrated=time.strftime("%Y-%m-%d %H:%M"))
        say(f"notes from {src} in place; conversations up to #{done} already looked at")
    else:
        notes.set_state(enabled=True, replayed_upto=0, migrated=time.strftime("%Y-%m-%d %H:%M"))
        say("switched to notes: the server learns them from past conversations, a few after each reply")
    if args.review:
        review()
    say(f"rollback: python tools/memory_notes.py off   (or: restore {dest})")
    status()


def review():
    from server import persona
    summarize, settings = summarizer()
    lang = persona.LANGUAGES.get(settings.get("language", "en"), persona.LANGUAGES["en"])["name"]
    did = asyncio.run(memory.review_notes(summarize, lang))
    say("she looked over what she knows:\n  " + ("\n  ".join(did) or "nothing to change"))


def restore(backup: Path):
    if not (backup / "log.jsonl").exists() and not (backup / "episodes.json").exists():
        sys.exit(f"{backup} doesn't look like a backup of her memory")
    safety = notes.backup("before-restore")
    keep = {"log.jsonl", "recall_tests.json", "recall_checks.jsonl"}   # what was said, and the recall tests
    for f in notes.DIR.iterdir():   # her memory as it was
        if f.is_file() and f.name not in keep and not (backup / f.name).exists():
            f.unlink()
    for f in backup.iterdir():
        if f.is_file() and f.name != "log.jsonl":
            shutil.copy2(f, notes.DIR / f.name)
    if not (backup / "notes.json").exists():
        notes.set_state(enabled=False)   # from before notes: stay with the fact list
    say(f"restored from {backup} (everything said since is still in log.jsonl and gets sorted again).")
    say(f"the state just before this restore: {safety}")
    status()


def positive(n: dict, keys: list[str]) -> bool:
    blob = " ".join([n.get("text", ""), n.get("subject", ""), " ".join(n.get("cues") or [])]).lower()
    return any(k.lower() in blob for k in keys)


def check():
    if not TESTS.exists():
        TESTS.write_text(json.dumps(DEFAULT_TESTS, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"(wrote the starter test set to {TESTS}: add your own questions there)")
    tests = json.loads(TESTS.read_text(encoding="utf-8"))
    store = notes.load()
    active = [n for n in store if n.get("status") == "active"]
    core = [n for n in active if n.get("tier") == "core"]
    archive = [n for n in active if n.get("tier") != "core"]
    labelled, unrelated = [t for t in tests if t["expect"]], [t for t in tests if not t["expect"]]
    from_core = from_archive = missing = 0
    arch_questions = arch_hits = 0
    rr, false_recalls = [], 0
    say(f"{len(active)} notes ({len(core)} core, {len(archive)} archive); {len(tests)} questions\n")
    for t in tests:
        q, keys = t["q"], t["expect"]
        picks = notes.recall(q, pool=archive)
        if not keys:
            false_recalls += bool(picks)
            say(f"  {'✓' if not picks else '✗'} {q}  (unrelated) " + (" | ".join(p["text"][:20] for p in picks) or "recalls nothing"))
            continue
        in_core = [n for n in core if positive(n, keys)]
        got = [p for p in picks if positive(p, keys)]
        if any(positive(n, keys) for n in archive):          # the answer lives in the archive: did recall find it?
            arch_questions += 1
            arch_hits += bool(got)
            ranked = notes.recall(q, k=len(archive) or 1, pool=archive)
            rank = next((i for i, p in enumerate(ranked, 1) if positive(p, keys)), None)
            rr.append(1 / rank if rank else 0.0)
        where = "archive" if got else "core" if in_core else None
        from_archive += where == "archive"
        from_core += where == "core"
        missing += where is None
        say(f"  {'✓' if where else '✗'} {q}  " + (f"from the {where}: " + (got or in_core)[0]["text"][:30] if where else "not in her context"))
    arch = f"{arch_hits}/{arch_questions}" if arch_questions else "n/a (no question's answer is in the archive yet)"
    say(f"\nin her context: {from_core + from_archive}/{len(labelled)} (core {from_core}, archive {from_archive}); "
        f"archive recall: {arch}" + (f", MRR {sum(rr) / len(rr):.2f}" if rr else "")
        + f"; unrelated questions recalling something: {false_recalls}/{len(unrelated)}")
    with CHECKS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time() * 1000), "notes": len(active), "core": len(core), "archive": len(archive),
                            "in_context": [from_core + from_archive, len(labelled)], "from_archive": from_archive,
                            "archive_recall": [arch_hits, arch_questions], "mrr": round(sum(rr) / len(rr), 3) if rr else None,
                            "false_recalls": [false_recalls, len(unrelated)],
                            "thresholds": {"COS_LO": notes.COS_LO, "COS_HI": notes.COS_HI, "R_MIN": notes.R_MIN}}) + "\n")
    say(f"(logged to {CHECKS})")
    if args.calibrate:
        import numpy as np
        pos, neg = [], []
        for t in tests:
            _, cos = notes.relevance(t["q"], active)
            for n, c in zip(active, cos):
                (pos if t["expect"] and positive(n, t["expect"]) else neg).append(float(c))
        if pos and neg:
            say(f"\ncalibration: related cos median {np.median(pos):.3f} (n={len(pos)}), unrelated 90th percentile "
                f"{np.percentile(neg, 90):.3f} (n={len(neg)}) -> COS_LO {np.percentile(neg, 90):.3f}, "
                f"COS_HI {max(np.percentile(pos, 75), np.percentile(neg, 90) + 0.03):.3f} (now {notes.COS_LO}, {notes.COS_HI})")


if args.command == "status":
    status()
elif args.command == "migrate":
    migrate()
elif args.command == "off":
    notes.set_state(enabled=False)
    say("notes off: she uses the old fact list (facts.json) again. Restart the server. ('on' switches back.)")
elif args.command == "on":
    notes.set_state(enabled=True)
    say("notes on. Restart the server.")
elif args.command == "restore":
    if not args.path:
        sys.exit("restore needs the backup folder (see data/memory-backups/)")
    restore(Path(args.path))
elif args.command == "check":
    check()
elif args.command == "review":
    review()
    status()
