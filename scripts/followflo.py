#!/usr/bin/env python3
"""
FollowFlo CLI — Phase 1〜3

会議の文字起こしテキストからタスクを抽出し、担当者への通知・完了報告・
次工程への自動引き継ぎを管理する。詳細は ../1.LEANINGS.md を参照。

抽出モード:
  --mode tag  : "# TASK: ..." タグ付き行を読む簡易パーサー(APIキー不要)
  --mode llm  : Anthropic APIで自由な会話形式の文字起こしからタスクを抽出(要 ANTHROPIC_API_KEY)
  未指定時    : ANTHROPIC_API_KEY が .env / 環境変数にあれば llm、無ければ tag にフォールバック

--mock-llm-response <file> : 実際のAPIを呼ばず、指定したJSONファイルの内容を
  LLMの応答として扱う。APIキーが無い環境でもパイプラインの配線確認ができる。

メール送信は行わず、data/outbox/ にメール内容(JSON)を書き出すところまでを担当する。
実際の送信はメール連携(Gmail API等)を別途配線すること。
"""
import json
import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
TASKS_FILE = BASE_DIR / "data" / "tasks.json"
OUTBOX_DIR = BASE_DIR / "data" / "outbox"
DIRECTORY_FILE = BASE_DIR / "data" / "directory.json"

TASK_LINE_RE = re.compile(
    r"#\s*TASK:\s*(?P<desc>.+?)\s*\|\s*担当:\s*(?P<name>[^<]+?)\s*<(?P<email>[^>]+)>\s*\|\s*依存:\s*(?P<dep>.+)"
)

LLM_SYSTEM_PROMPT = """あなたは会議の文字起こしからアクションアイテム(タスク)を抽出するアシスタントです。
入力される文字起こしを読み、実行すべきタスクを全て抽出してください。

出力は必ず以下のJSON形式の配列のみとしてください(説明文や前置きは一切不要):
[
  {
    "description": "タスクの内容(簡潔に)",
    "owner_name": "担当者の名前(文字起こし中の呼び方のまま)",
    "depends_on_description": "このタスクが着手できる前提となる、他のタスクのdescriptionと同一文字列。無ければnull"
  }
]

ルール:
- 雑談や単なる感想はタスクとして抽出しない。
- 「〜してください」「〜を担当してください」「〜までに」など、明確に依頼・合意されたものだけをタスクとする。
- depends_on_description は同じ抽出結果内の他タスクのdescriptionと完全に一致させること。
"""


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_tasks():
    return load_json(TASKS_FILE, [])


def save_tasks(tasks):
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


def load_directory():
    return load_json(DIRECTORY_FILE, {})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def extract_tasks_tag_based(text):
    """Phase 1: '# TASK: ...' タグ行から抽出する簡易パーサー。APIキー不要。"""
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


def extract_tasks_llm(text, mock_response_path=None):
    """Phase 3: Anthropic APIで自由な会話文字起こしからタスクを抽出する。"""
    directory = load_directory()

    if mock_response_path:
        raw = json.loads(Path(mock_response_path).read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY が設定されていません。.env.example を .env にコピーして"
                "キーを設定するか、--mode tag を使ってください。"
            )
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            system=LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        content = resp.content[0].text
        # コードフェンス等が混ざっても壊れないよう、JSON配列部分だけ取り出す
        start = content.find("[")
        end = content.rfind("]") + 1
        raw = json.loads(content[start:end])

    found = []
    for item in raw:
        name = item["owner_name"].strip()
        email = directory.get(name)
        if not email:
            print(f"警告: 「{name}」さんのメールアドレスが data/directory.json にありません。"
                  f"手動で登録してください。このタスクは owner_email 未設定のまま登録します。")
        found.append({
            "description": item["description"].strip(),
            "owner_name": name,
            "owner_email": email or "",
            "dep_desc": item.get("depends_on_description") or None,
        })
    return found


def cmd_extract(args):
    text = Path(args.transcript).read_text(encoding="utf-8")

    mode = args.mode
    if mode is None:
        mode = "llm" if (os.environ.get("ANTHROPIC_API_KEY") or args.mock_llm_response) else "tag"

    if mode == "llm":
        print("抽出モード: llm (Anthropic API)")
        raw_tasks = extract_tasks_llm(text, mock_response_path=args.mock_llm_response)
    else:
        print("抽出モード: tag (簡易パーサー・APIキー不要)")
        raw_tasks = extract_tasks_tag_based(text)

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
            "depends_on": None,
            "status": "pending",
            "evidence": None,
            "created_at": now_iso(),
            "completed_at": None,
        })

    for t in new_tasks:
        if t["dep_desc"]:
            t["depends_on"] = desc_to_id.get(t["dep_desc"])
            t["status"] = "waiting" if t["depends_on"] else "ready"
        else:
            t["status"] = "ready"

    tasks.extend(new_tasks)
    save_tasks(tasks)

    print(f"{len(new_tasks)}件のタスクを登録しました。")
    for t in new_tasks:
        dep_note = f"(依存: {t['depends_on']})" if t["depends_on"] else ""
        email_note = t["owner_email"] or "★メール未設定"
        print(f"  [{t['id']}] {t['description']} → {t['owner_name']} <{email_note}> status={t['status']} {dep_note}")


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
    if not task["owner_email"]:
        print(f"タスク {task['id']} は担当者のメールアドレスが未設定です。data/directory.json に追加してから再実行してください。")
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

    unlocked = [t for t in tasks if t.get("depends_on") == task["id"] and t["status"] == "waiting"]
    for nt in unlocked:
        nt["status"] = "ready"
    save_tasks(tasks)

    for nt in unlocked:
        cmd_notify(argparse.Namespace(task_id=nt["id"]))
        print(f"  → 次工程 [{nt['id']}] {nt['description']} を自動的に {nt['owner_name']} さんへ引き継ぎました。")


def main():
    parser = argparse.ArgumentParser(description="FollowFlo CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_extract = sub.add_parser("extract", help="文字起こしテキストからタスクを抽出")
    p_extract.add_argument("transcript")
    p_extract.add_argument("--leader-email", default="keepmetal666@gmail.com")
    p_extract.add_argument("--mode", choices=["tag", "llm"], default=None,
                            help="抽出方式。未指定ならAPIキーの有無で自動判定")
    p_extract.add_argument("--mock-llm-response", default=None,
                            help="APIを呼ばずこのJSONファイルをLLM応答として使う(テスト用)")
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
