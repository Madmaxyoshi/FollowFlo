#!/usr/bin/env python3
"""
FollowFlo CLI — Phase 1 MVP

会議の文字起こしテキストからタスクを抽出し、担当者への通知・完了報告・
次工程への自動引き継ぎを管理する。詳細は ../1.LEANINGS.md を参照。

Phase 1の割り切り:
- タスク抽出は "# TASK: ..." というタグ付き行を読む簡易パーサー(extract_tasks_from_text)。
  Phase 3で、この関数の中身をLLM API呼び出しに差し替える想定(インターフェースは維持)。
- メール送信は行わず、data/outbox/ にメール内容(JSON)を書き出すところまでを担当する。
  実際の送信はメール連携(Gmail API等)を別途配線すること。今回のデモでは
  Claude(このプロジェクトのアシスタント)がoutboxの内容を読み、Gmail経由で実際に送信して動作確認した。
"""
import json
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
TASKS_FILE = BASE_DIR / "data" / "tasks.json"
OUTBOX_DIR = BASE_DIR / "data" / "outbox"

TASK_LINE_RE = re.compile(
    r"#\s*TASK:\s*(?P<desc>.+?)\s*\|\s*担当:\s*(?P<name>[^<]+?)\s*<(?P<email>[^>]+)>\s*\|\s*依存:\s*(?P<dep>.+)"
)


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    return json.loads(TASKS_FILE.read_text(encoding="utf-8"))


def save_tasks(tasks):
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def extract_tasks_from_text(text):
    """タスク抽出のインターフェース。Phase 1は簡易タグパーサー、Phase 3でLLM化する。"""
    found = []
    for line in text.splitlines():
        m = TASK_LINE_RE.search(line)
        if not m:
            continue
        found.append({
            "description": m.group("desc").strip(),
            "owner_name": m.group("name").strip(),
            "owner_email": m.group("email").strip(),
            "dep_desc": None if m.group("dep").strip() == "なし" else m.group("dep").strip(),
        })
    return found


def cmd_extract(args):
    text = Path(args.transcript).read_text(encoding="utf-8")
    raw_tasks = extract_tasks_from_text(text)
    if not raw_tasks:
        print("タスクが見つかりませんでした。")
        return

    tasks = load_tasks()
    start_id = len(tasks) + 1
    desc_to_id = {t["description"]: t["id"] for t in tasks}

    new_tasks = []
    for i, rt in enumerate(raw_tasks):
        task_id = f"T{start_id + i}"
        desc_to_id[rt["description"]] = task_id
        new_tasks.append({
            "id": task_id,
            "description": rt["description"],
            "owner_name": rt["owner_name"],
            "owner_email": rt["owner_email"],
            "leader_email": args.leader_email,
            "dep_desc": rt["dep_desc"],
            "depends_on": None,  # 後で解決
            "status": "pending",  # pending -> ready -> notified -> done
            "evidence": None,
            "created_at": now_iso(),
            "completed_at": None,
        })

    # 依存関係をIDで解決
    for t in new_tasks:
        if t["dep_desc"]:
            t["depends_on"] = desc_to_id.get(t["dep_desc"])
            t["status"] = "waiting"  # 前工程待ち
        else:
            t["status"] = "ready"

    tasks.extend(new_tasks)
    save_tasks(tasks)

    print(f"{len(new_tasks)}件のタスクを登録しました。")
    for t in new_tasks:
        dep_note = f"(依存: {t['depends_on']})" if t["depends_on"] else ""
        print(f"  [{t['id']}] {t['description']} → {t['owner_name']} <{t['owner_email']}> status={t['status']} {dep_note}")
    print("\n次のステップ: `python scripts/followflo.py list` で確認し、"
          "ready状態のタスクを `notify` してください。")


def cmd_list(args):
    tasks = load_tasks()
    if not tasks:
        print("タスクはまだ登録されていません。")
        return
    for t in tasks:
        print(f"[{t['id']}] status={t['status']:9s} {t['description']}  担当:{t['owner_name']}<{t['owner_email']}>"
              + (f"  依存元:{t['depends_on']}" if t["depends_on"] else ""))


def write_outbox(to_email, subject, body, tag):
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    fname = OUTBOX_DIR / f"{now_iso().replace(':','-')}_{tag}.json"
    fname.write_text(json.dumps({"to": to_email, "subject": subject, "body": body}, ensure_ascii=False, indent=2), encoding="utf-8")
    return fname


def cmd_notify(args):
    tasks = load_tasks()
    task = next((t for t in tasks if t["id"] == args.task_id), None)
    if not task:
        print(f"タスク {args.task_id} が見つかりません。")
        return
    if task["status"] == "waiting":
        print(f"タスク {task['id']} はまだ前工程({task['depends_on']})待ちのため通知できません。")
        return

    subject = f"[FollowFlo] タスク依頼: {task['description']}"
    body = (
        f"{task['owner_name']} さん\n\n"
        f"以下のタスクが割り当てられました。\n\n"
        f"タスク: {task['description']}\n\n"
        f"完了したら次のコマンドで報告してください(または会議リーダーに直接連絡):\n"
        f"  python scripts/followflo.py complete {task['id']} --evidence \"完了内容\"\n\n"
        f"— FollowFlo"
    )
    path = write_outbox(task["owner_email"], subject, body, f"notify_{task['id']}")
    task["status"] = "notified"
    save_tasks(tasks)
    print(f"通知メールを outbox に作成しました: {path}")


def cmd_complete(args):
    tasks = load_tasks()
    task = next((t for t in tasks if t["id"] == args.task_id), None)
    if not task:
        print(f"タスク {args.task_id} が見つかりません。")
        return

    task["status"] = "done"
    task["evidence"] = args.evidence
    task["completed_at"] = now_iso()

    # リーダーへの完了報告
    subject = f"[FollowFlo] タスク完了報告: {task['description']}"
    body = (
        f"{task['owner_name']} さんが以下のタスクを完了しました。\n\n"
        f"タスク: {task['description']}\n"
        f"エビデンス: {task['evidence']}\n"
        f"完了日時: {task['completed_at']}\n\n"
        f"— FollowFlo"
    )
    write_outbox(task["leader_email"], subject, body, f"leader_{task['id']}")
    print(f"タスク {task['id']} を完了にしました。リーダーへの通知をoutboxに作成しました。")

    # 依存していたタスクを自動的にreadyにして通知メールも作成(自動引き継ぎ)
    unlocked = [t for t in tasks if t.get("depends_on") == task["id"] and t["status"] == "waiting"]
    save_tasks(tasks)  # 先に完了状態を保存

    for nt in unlocked:
        nt["status"] = "ready"
    if unlocked:
        save_tasks(tasks)

    for nt in unlocked:
        cmd_notify(argparse.Namespace(task_id=nt["id"]))
        print(f"  → 次工程 [{nt['id']}] {nt['description']} を自動的に {nt['owner_name']} さんへ引き継ぎました。")


def main():
    parser = argparse.ArgumentParser(description="FollowFlo CLI (Phase 1 MVP)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_extract = sub.add_parser("extract", help="文字起こしテキストからタスクを抽出")
    p_extract.add_argument("transcript")
    p_extract.add_argument("--leader-email", default="keepmetal666@gmail.com")
    p_extract.set_defaults(func=cmd_extract)

    p_list = sub.add_parser("list", help="タスク一覧を表示")
    p_list.set_defaults(func=cmd_list)

    p_notify = sub.add_parser("notify", help="タスクを担当者に通知")
    p_notify.add_argument("task_id")
    p_notify.set_defaults(func=cmd_notify)

    p_complete = sub.add_parser("complete", help="タスク完了を報告")
    p_complete.add_argument("task_id")
    p_complete.add_argument("--evidence", required=True)
    p_complete.set_defaults(func=cmd_complete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
