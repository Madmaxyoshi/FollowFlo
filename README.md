# FollowFlo

会議で決まったタスクが実行されないまま放置され、進捗確認のためだけの会議が繰り返される悪循環を断ち切るためのツール。

**まずは `1.LEANINGS.md` を読んでください。** 課題定義・エビデンス・設計方針・現在の状況がまとまっています。

## 現在のスコープ（Phase 1 / MVP）

- 入力: 会議の文字起こしテキスト（音声のリアルタイム処理はPhase 2）
- タスク抽出: 簡易ルールベース（LLM API接続はPhase 3）
- 通知: Gmail経由でメール送信
- 完了報告・自動引き継ぎ: `depends_on` による依存関係チェーン

## 使い方

```bash
# 1. 文字起こしテキストからタスクを抽出して登録
python scripts/followflo.py extract transcripts/sample_meeting.txt

# 2. 登録されたタスク一覧を確認
python scripts/followflo.py list

# 3. タスクを担当者に通知（メール送信）
python scripts/followflo.py notify <task_id>

# 4. タスク完了を報告（エビデンス付き）→ リーダー通知＋次工程への自動引き継ぎ
python scripts/followflo.py complete <task_id> --evidence "完了内容の説明"
```

## ディレクトリ構成

```
FollowFlo/
├── 1.LEANINGS.md      # 必読：課題定義・エビデンス・設計方針・進捗ログ
├── README.md
├── data/
│   └── tasks.json      # タスク台帳
├── scripts/
│   └── followflo.py    # CLI本体
└── transcripts/
    └── sample_meeting.txt  # 動作確認用サンプル
```
