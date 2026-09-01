# FollowFlo

会議で決まったタスクが実行されないまま放置され、進捗確認のためだけの会議が繰り返される悪循環を断ち切るためのツール。

**まずは `1.LEANINGS.md` を読んでください。** 課題定義・エビデンス・設計方針・現在の状況がまとまっています。

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env   # .env に実際のANTHROPIC_API_KEYを設定(Phase3のLLM抽出を使う場合)
```

`data/directory.json` に、会議参加者の「呼ばれ方の名前 → メールアドレス」を登録しておくこと。
LLM抽出は文字起こし中の名前しか分からないため、ここでメールアドレスに変換する。

## 現在のスコープ

- **Phase 1(タグベース抽出)**: `# TASK: 内容 | 担当: 名前 <email> | 依存: なし/依存先の内容` という
  タグ行を書いておけば、APIキー無しでタスク抽出できる。
- **Phase 3(LLM抽出・実装済み)**: 自然な会話形式の文字起こしから、Anthropic APIでタスクを自動抽出する。
  `ANTHROPIC_API_KEY` が `.env` にあれば自動的にこちらが使われる。
- **Phase 2(マイクからのリアルタイム音声取得)**: 未実装。今は文字起こし済みテキストが入力。
- 通知・完了報告・自動引き継ぎは実装済み(メール送信自体は`data/outbox/`に書き出すところまで。実送信は別途配線)。

## 使い方

```bash
# 1. 文字起こしテキストからタスクを抽出して登録(APIキーがあれば自動でllmモード)
python scripts/followflo.py extract transcripts/sample_meeting_natural.txt

# APIキーが無い/使いたくない場合は明示的にtagモード
python scripts/followflo.py extract transcripts/sample_meeting.txt --mode tag

# APIキー無しでLLM抽出の配線だけ確認したい場合(モック応答を使う)
python scripts/followflo.py extract transcripts/sample_meeting_natural.txt \
  --mode llm --mock-llm-response data/mock_llm_response_example.json

# 2. 登録されたタスク一覧を確認
python scripts/followflo.py list

# 3. タスクを担当者に通知(outboxにメール内容を書き出す)
python scripts/followflo.py notify <task_id>

# 4. タスク完了を報告(エビデンス付き)→ リーダー通知＋次工程への自動引き継ぎ
python scripts/followflo.py complete <task_id> --evidence "完了内容の説明"
```

## ディレクトリ構成

```
FollowFlo/
├── 1.LEANINGS.md              # 必読：課題定義・エビデンス・設計方針・進捗ログ
├── README.md
├── requirements.txt
├── .env.example                # コピーして .env にAPIキーを設定
├── data/
│   ├── tasks.json               # タスク台帳(gitignore対象)
│   ├── directory.json           # 氏名→メールアドレスの対応表
│   └── mock_llm_response_example.json  # LLM応答のモック(テスト用)
├── scripts/
│   └── followflo.py            # CLI本体(Phase1タグ抽出 / Phase3 LLM抽出)
└── transcripts/
    ├── sample_meeting.txt            # タグ付きサンプル(Phase1用)
    └── sample_meeting_natural.txt    # 自然な会話サンプル(Phase3用)
```
