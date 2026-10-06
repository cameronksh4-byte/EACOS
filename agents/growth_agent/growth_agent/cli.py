"""Command line:  python -m growth_agent <command>

  brainstorm [--count N] [--focus TEXT]   generate + score + rank new ideas
  ideas                                   list ideas, best first
  launch IDEA_ID [--rebuild]              launch kit + landing page + queue posts
  queue [--status S]                      show scheduled posts
  approve POST_ID|all  /  reject POST_ID  (only needed with approval: manual)
  publish                                 publish posts that are due now
  autopilot                               publish due posts + refill content
  pause IDEA_ID / resume IDEA_ID          stop/start posting for an idea
"""

import argparse
import json
import os
from pathlib import Path

import yaml

from .autopilot import publish_due, refill_content
from .graphs import build_brainstorm_graph, build_launch_graph
from .landing import write_landing
from .llm import FakeLLM, get_llm
from .publishers import get_publisher
from .store import Store

HERE = Path(__file__).resolve().parent.parent


def load_profile(path: str | None) -> dict:
    candidates = [path, os.environ.get("GROWTH_AGENT_PROFILE"),
                  HERE / "profile.yaml", HERE / "profile.example.yaml"]
    for c in candidates:
        if c and Path(c).exists():
            return yaml.safe_load(Path(c).read_text()) or {}
    return {}


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="growth_agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="path to profile.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brainstorm")
    b.add_argument("--count", type=int, default=8)
    b.add_argument("--focus", default="")
    sub.add_parser("ideas")
    lp = sub.add_parser("launch")
    lp.add_argument("idea_id")
    lp.add_argument("--rebuild", action="store_true", help="regenerate the launch kit")
    q = sub.add_parser("queue")
    q.add_argument("--status")
    for name in ("approve", "reject"):
        sub.add_parser(name).add_argument("post_id")
    for name in ("pause", "resume"):
        sub.add_parser(name).add_argument("idea_id")
    sub.add_parser("publish")
    sub.add_parser("autopilot")
    args = ap.parse_args(argv)

    profile = load_profile(args.profile)
    store = Store()
    llm = get_llm()
    if isinstance(llm, FakeLLM) and args.cmd in ("brainstorm", "launch", "autopilot"):
        print("(no ANTHROPIC_API_KEY - using offline sample answers)\n")

    if args.cmd == "brainstorm":
        out = build_brainstorm_graph(llm, store).invoke(
            {"profile": profile, "count": args.count, "focus": args.focus})
        print_ideas([store.idea(i) for i in out["saved_ids"]])
        print("\nNext: python -m growth_agent launch <ID>")

    elif args.cmd == "ideas":
        print_ideas(store.ideas())

    elif args.cmd == "launch":
        out = build_launch_graph(llm, store).invoke(
            {"profile": profile, "idea_id": args.idea_id, "rebuild_kit": args.rebuild})
        idea = store.idea(args.idea_id)
        kit = store.data["launch_kits"][idea["id"]]
        form = profile.get("lead_capture", {}).get("form_action", "#")
        page = write_landing(idea, kit, form, store.path.parent)
        posts = [store.post(i) for i in out["queued_ids"]]
        print(f"🚀 Launched {idea['name']}\n   {kit['headline']}\n")
        print(f"Founding offer: {kit['founding_offer']}\n")
        print("14-day plan:")
        for step in kit["launch_plan"]:
            print(f"  Day {step['day']:>2}: {step['task']}")
        print(f"\nLanding page: {page}")
        print(f"Queued {len(posts)} posts "
              f"({sum(p['status'] == 'rejected' for p in posts)} held back by review).")

    elif args.cmd == "queue":
        for p in store.posts(status=args.status):
            print(f"{p['scheduled_for'][:16]}  {p['status']:<9} {p['platform']:<9} "
                  f"{p['id']}\n    {p['text'][:110]!r}")

    elif args.cmd in ("approve", "reject"):
        targets = store.posts(status="pending") if args.post_id == "all" \
            else [store.post(args.post_id)]
        for p in targets:
            p["status"] = "approved" if args.cmd == "approve" else "rejected"
        store.save()
        print(f"{args.cmd}d {len(targets)} post(s)")

    elif args.cmd in ("pause", "resume"):
        idea = store.idea(args.idea_id)
        idea["status"] = "paused" if args.cmd == "pause" else "launched"
        for p in store.posts(idea_id=idea["id"]):
            if args.cmd == "pause" and p["status"] in ("approved", "pending"):
                p["status"], p["paused_from"] = "paused", p["status"]
            elif args.cmd == "resume" and p["status"] == "paused":
                p["status"] = p.pop("paused_from", "approved")
        store.save()
        print(f"{idea['name']} is now {idea['status']}")

    elif args.cmd in ("publish", "autopilot"):
        publisher = get_publisher(profile.get("social", {}), store.path.parent)
        done = publish_due(store, publisher)
        print(f"Published {sum(p['status'] == 'posted' for p in done)} / {len(done)} due posts "
              f"via {publisher.name}")
        for p in done:
            if p["status"] != "posted":
                print(f"  ! {p['id']}: {p.get('last_error')}")
        if args.cmd == "autopilot":
            added = refill_content(store, llm, profile)
            print(f"Refilled content: {json.dumps(added) if added else 'queue is healthy'}")


def print_ideas(ideas: list[dict]):
    for i in ideas:
        s = i.get("score", {})
        print(f"[{s.get('total', '?'):>3}] {i['name']}  ({i['kind']}, {i['status']})  id={i['id']}")
        print(f"      {i['one_liner']}")
        print(f"      lead magnet: {i['lead_magnet']}  |  money: {i['monetization']}")
