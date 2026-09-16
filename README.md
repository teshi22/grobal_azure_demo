# 出張申請エージェント比較アプリ

自然言語で受け付けた出張依頼を、情報確認、旅程検索、旅費規程チェック、申請書作成、送信まで進めるWebアプリケーションです。通常の対話画面に加え、Agent Frameworkワークフローと単一Prompt Agentを同じテストケースで比較する評価画面を備えています。

通常のエージェント処理は、**Microsoft Agent Frameworkで定義した1つのワークフロー**としてMicrosoft Foundry Hosted Agent上で実行します。依頼整理、旅程作成、規程説明、申請案作成は、versionを固定した4つのFoundry Prompt Agentが担当します。Azure Container Apps上のFastAPIは、認証、会話所有権、HITL（Human-in-the-Loop）、SSE配信、Foundryバッチ評価を受け持つBFFです。

初めて触る場合は「構成」「処理の流れ」「Azure へデプロイする」まで読んでください。環境変数、テスト、運用上の注意は必要なときに参照できます。

## 4つのPrompt AgentをHosted Agentのワークフローから呼ぶ

```mermaid
flowchart LR
    User["利用者"]

    subgraph CA["Azure Container Apps"]
        Web["Next.js<br/>静的フロントエンド"]
        BFF["FastAPI BFF<br/>認証・所有権・SSE"]
    end

    subgraph Foundry["Microsoft Foundry"]
        Host["Hosted Agent<br/>Responses protocol 2.0.0"]
        subgraph AF["Agent Framework workflow"]
            Clarifier["Request Clarifier"]
            Planner["Travel Planner<br/>Web Search"]
            Policy["Policy Checker"]
            Writer["Approval Writer"]
        end
        Single["Single Prompt Agent<br/>比較シナリオ"]
        Evaluation["Foundry Evaluation<br/>同一Dataset・同一rubric"]
        Model["GPT model deployment"]
    end

    subgraph Function["Azure Functions"]
        MCP["MCP<br/>submit_travel_request"]
    end

    Cosmos[("Azure Cosmos DB<br/>会話・HITL・イベント<br/>申請・評価結果")]
    Insights["Application Insights"]

    User --> Web
    Web -->|"REST / SSE"| BFF
    BFF -->|"Responses API"| Host
    Host --> AF
    Clarifier & Planner & Policy & Writer -->|"Prompt Agent"| Model
    Planner -->|"Web Search"| Model
    BFF --> Evaluation
    Evaluation --> Host
    Evaluation --> Single
    Single -->|"Web Search"| Model
    Writer --> MCP
    BFF --> Cosmos
    Host --> Cosmos
    MCP --> Cosmos
    BFF & Host --> Insights
```

| コンポーネント | 実装 | 主な責務 |
|---|---|---|
| Frontend | Next.js 15、React 19 | チャット、確認画面、申請一覧、比較評価 |
| BFF | FastAPI | Entra ID認証、会話所有権、Hosted Agent呼び出し、durable SSE、Datasetと評価runの管理 |
| Hosted Agent | Agent Framework、Responses protocol 2.0.0 | 4つのPrompt AgentのオーケストレーションとHITL |
| Prompt Agents | Foundry Agent Service | 専門処理4種と単一エージェント比較シナリオ |
| MCP | Azure Functions | 承認済み申請の冪等な登録 |
| Data | Azure Cosmos DB | 会話、イベント、チェックポイント、承認grant、申請、評価ケースと結果 |
| Observability | OpenTelemetry、Application Insights | BFF と Hosted Agent のトレース |
| Infrastructure | Bicep、Azure Developer CLI | Japan East への一括デプロイと RBAC 設定 |

Hosted Agent 内の名前はチェックポイントとの互換性に関わるため、運用開始後は変更しないでください。

| 種別 | 固定値 |
|---|---|
| Hosted Agent | `travel-request-agent` |
| Workflow | `travel-request-workflow` |
| Prompt Agents | `travel-request-clarifier`、`travel-request-planner`、`travel-request-policy-narrator`、`travel-request-approval-writer` |
| Single Prompt Agent | `travel-request-single-agent` |

Prompt Agentは名前だけでなくversionも設定に保存します。プロンプトやツール定義を変更した場合は新しいversionを作り、Hosted Agentと評価runへ同じversionを渡します。

## 同じ20ケースで2つの構成を比較する

`/evaluations`では次の2シナリオを比較します。

| シナリオ | 構成 |
|---|---|
| Agent Framework workflow | Hosted Agentが処理順と状態を管理し、4つのPrompt Agentと決定論的な規程判定を組み合わせる |
| Single Prompt Agent | 1つのPrompt Agentが依頼整理、Web検索、規程判断、申請案作成までを処理する |

初期ケースは`evaluation-data/travel-request-cases.jsonl`に20件あります。標準的な国内出張、情報不足、規程の境界条件、複雑な経路、相対日付を含みます。画面から追加・編集・複製でき、JSONLも取り込めます。

自動評価はMicrosoft Foundryのバッチ評価を土台にします。旅行申請用rubricを60%、JSON Schema、必須項目、日付、規程、運賃根拠、金額合計の決定論的検証を40%として総合精度を算出します。Foundryの補助指標は総合点に混ぜず、原因分析用に別表示します。

比較画面では総合精度に加え、バッチ所要時間、モデル別トークン量、推定コストを確認できます。推定コストは`app/backend/config/model-pricing.json`の単価を使い、更新日も表示します。単価がないモデルは0円にせず、算出不可として扱います。

人手評価はケースごとに両シナリオを1〜5点で採点し、勝者または引き分けとコメントを保存します。自動評価と人手評価は別に集計するため、rubricでは高得点でも人が読みにくいケースなどを確認できます。

評価モードは申請案の生成で停止します。依頼確認と旅程レビューは自動通過しますが、情報不足は推測せず確認事項を返します。MCP、approval grant、`travel-requests`への書き込みは実行しません。

## 申請は4回の確認を挟み、安全に再開できる

```mermaid
flowchart TD
    Start["出張依頼"] --> Clarify["依頼内容を構造化"]
    Clarify --> Complete{"出発地・目的地・日程・目的が揃ったか"}
    Complete -->|不足| Ask["不足情報を確認<br/>request_info"] --> Clarify
    Complete -->|揃った| Confirm["依頼内容を確認<br/>request_info"]
    Confirm --> Plan["Web Search で旅程を作成"]
    Plan --> Review["旅程をレビュー<br/>request_info"]
    Review -->|修正| Plan
    Review -->|承認| Rules["決定論的な旅費規程判定"]
    Rules --> Compliant{"規程に適合したか"}
    Compliant -->|不適合| Replan["違反理由を反映"] --> Plan
    Compliant -->|適合| Document["申請書を作成"]
    Document --> SubmitConfirm["送信を最終確認<br/>request_info"]
    SubmitConfirm -->|取消| Cancel["送信せず終了"]
    SubmitConfirm -->|承認| Grant["短命な approval grant を発行"]
    Grant --> MCP["MCP で冪等に登録"]
    MCP --> Done["申請完了"]
```

HITL は Agent Framework の `request_info` を使います。BFF は `call_id`、`request_id`、直前の Responses ID を Cosmos DB に保存し、ブラウザから届いた回答を `function_call_output` に変換して同じ会話を再開します。

受け付けたメッセージも会話ドキュメントに保存し、BFF レプリカが lease を取得して処理します。レプリカが途中で停止した場合は、lease の期限後に別レプリカが残作業を引き継ぎます。

Hosted Agent のチェックポイントも Cosmos DB に保存します。プロセス再起動後に再開できる一方、最後のチェックポイント以降が再実行される可能性があるため、MCP の書き込みは冪等化しています。

## 認証情報と所有権をBFFから副作用まで引き継ぐ

Container Apps の Easy Auth が利用者を Microsoft Entra ID で認証します。FastAPI は `x-ms-client-principal` または署名済み Bearer JWT を検証し、認証済みユーザーの object ID を会話所有者として保存します。

BFF は所有者が一致する場合だけ、会話の取得、HITL 回答、SSE 接続、申請一覧取得を許可します。リクエスト本文の `user_id` は信頼しません。

送信承認後は、BFF が次の情報を持つ短命な approval grant を発行します。

- 会話 ID と認証済みユーザー ID
- 承認した旅程の SHA-256 ハッシュ
- 冪等性キー
- 有効期限

MCP は grant の状態、有効期限、会話 ID、旅程ハッシュを再検証します。申請 ID は冪等性キーから決定的に生成し、Cosmos DB の conditional create と ETag 更新で同時実行時の重複登録を防ぎます。

## リポジトリはBFF、Hosted Agent、MCPを分離している

```text
.
├── app/
│   ├── Dockerfile
│   ├── frontend/                       # Next.js UI
│   └── backend/
│       ├── app/
│       │   ├── auth/entra.py           # Easy Auth / Bearer JWT 検証
│       │   ├── routers/                # conversations、stream、travel_requests、evaluations
│       │   └── services/               # Hosted Agent、Foundry評価、採点、Cosmos
│       ├── config/                     # rubric とモデル単価
│       └── tests/
├── prompt-agents/                      # 5つのPrompt Agent定義
├── evaluation-data/
│   └── travel-request-cases.jsonl      # 初期20ケース
├── hosted-agent/
│   ├── main.py                         # ResponsesHostServer エントリーポイント
│   ├── Dockerfile                      # Hosted Agent、port 8088
│   ├── travel_agent/
│   │   ├── agents.py                   # version固定Prompt Agent接続
│   │   ├── workflow.py                 # Agent Framework グラフ
│   │   ├── executors.py                # HITL、評価モード、規程判定、MCP実行
│   │   └── checkpoints.py              # Cosmos チェックポイント
│   └── tests/
├── mcp-tools/
│   ├── function_app.py                 # MCP JSON-RPC エンドポイント
│   ├── tools/submit_travel_request.py  # grant 検証と冪等登録
│   └── tests/
├── infra/
│   ├── main.bicep
│   └── modules/
├── scripts/
│   ├── deploy-prompt-agents.py         # Prompt Agentの同期
│   ├── validate-evaluation-data.py     # 初期JSONLの検証
│   ├── configure-hosted-agent.sh       # Hosted Agent MI の RBAC と Easy Auth
│   └── smoke-hosted-agent.py
├── azure.yaml                          # Foundry Hosted Agent デプロイ定義
├── deploy.sh                           # ローカル端末からの一括デプロイ
└── .github/workflows/deploy.yml        # GitHub Actions
```

## Azure へデプロイする

### 前提を揃える

- Azure CLI
- Azure Developer CLI（`azd`）
- Python 3.11
- `jq` と `zip`
- Azure サブスクリプションでリソースとロール割り当てを作成できる権限
- Microsoft Foundry Hosted Agent を利用できるサブスクリプション
- Agent Framework が対応する GPT モデルデプロイ

すべての Azure リソースは既定で `japaneast` に作成します。既存の Foundry アカウントは別リージョンへ移せません。旧構成から切り替える場合は、新しいリソースグループへのデプロイを推奨します。

Cosmos DB のパーティションキーも変更できません。旧コンテナーが異なるキーを使っている場合は、新規コンテナーへの移行または再作成が必要です。

### 手元から一括デプロイする

```bash
az login
az account set --subscription <subscription-id>
bash deploy.sh
```

`deploy.sh` は次の順に処理します。

1. BFF と MCP 用の Entra ID アプリ登録を作成または再利用する
2. Bicep で Foundry、Cosmos DB、Functions、Container Apps を構築する
3. 5つのPrompt Agent定義を同期し、確定したversionを保存する
4. MCP Functions、Hosted Agent、BFFを固定versionでデプロイする
5. BFF、Foundryプロジェクト、Hosted AgentのManaged Identityへ必要な権限を設定する
6. 初期20ケースを登録する
7. BFFのhealth check、Hosted Agentの`request_info`、2ケースの比較評価を確認する

必要に応じて環境変数でデプロイ先を上書きできます。

```bash
AZURE_SUBSCRIPTION_ID=<subscription-id> \
RESOURCE_GROUP=rg-travel-agent-prod \
LOCATION=japaneast \
AZURE_ENV_NAME=travel-agent-prod \
MODEL_DEPLOYMENT_NAME=gpt-5.4 \
bash deploy.sh
```

既存のアプリ登録を使う場合は、`MCP_ENTRA_CLIENT_ID` と `WEB_ENTRA_CLIENT_ID` も指定してください。スクリプトはデプロイ結果をもとに、ルート、`app/backend`、`hosted-agent` の `.env` を生成します。

### GitHub Actions から継続デプロイする

`.github/workflows/deploy.yml` は `main` への push または手動実行で、インフラからスモークテストまで進めます。

OIDC 用の GitHub Secrets:

| Secret | 内容 |
|---|---|
| `AZURE_CLIENT_ID` | GitHub OIDC で使うサービスプリンシパルの client ID |
| `AZURE_TENANT_ID` | Entra tenant ID |
| `AZURE_SUBSCRIPTION_ID` | デプロイ先 subscription ID |
| `MCP_ENTRA_CLIENT_ID` | MCP Functions を表す Entra アプリの client ID |
| `WEB_ENTRA_CLIENT_ID` | Container Apps Easy Auth 用 Entra アプリの client ID |

任意の GitHub Variables:

| Variable | 既定値 |
|---|---|
| `AZURE_ENV_NAME` | `travel-agent-prod` |
| `AZURE_RESOURCE_GROUP` | `rg-travel-agent-hosted-demo` |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `gpt-5.4` |
| `AZURE_AI_MODEL_CAPACITY` | `20`（20K TPM） |

GitHub OIDC のサービスプリンシパルには、リソース作成と RBAC 割り当てに加え、BFF 用アプリ登録の redirect URI を更新できる Microsoft Graph 権限または所有権が必要です。

## ローカルでは各プロセスを分けて確認する

依存関係をまとめて入れる場合:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.sample app/backend/.env
cp hosted-agent/.env.example hosted-agent/.env
```

Windows PowerShell では有効化コマンドを `.venv\Scripts\Activate.ps1` に読み替えてください。

ローカル BFF は Entra 設定が空の場合だけ `dev-user` を使います。BFF の接続先は `app/backend/.env`、Hosted Agent の接続先は `hosted-agent/.env` に設定してください。

```bash
# BFF
cd app/backend
uvicorn app.main:app --reload --port 8000

# Frontend
cd app/frontend
npm ci
npm run dev

# Hosted Agent のローカル起動
cd hosted-agent
python main.py
```

Hosted Agent のローカル起動ではインメモリチェックポイントを使います。Foundry 上では `COSMOS_ENDPOINT` が設定されるため、Cosmos DB の永続チェックポイントに切り替わります。

Azureへ接続せず比較画面を確認する場合は、BFFの明示的な評価スタブ設定を有効にします。スタブはローカル専用で、本番では自動的に有効になりません。実際の評価ではPrompt Agentのversion、Dataset version、judge modelが必要です。

初期JSONLは次のコマンドで検証できます。

```bash
python scripts/validate-evaluation-data.py
```

## 変更前に3つのテスト群とFrontendを確認する

```bash
python -m pytest hosted-agent/tests app/backend/tests mcp-tools/tests
python scripts/validate-evaluation-data.py

cd app/frontend
npm ci
npm run build

cd ../..
az bicep build --file infra/main.bicep
azd show
```

Hosted Agent のスモークテストは、デプロイ後の Responses API を直接呼び、最初の確認要求が `request_info` として返ることを検証します。

```bash
python scripts/smoke-hosted-agent.py \
  --project-endpoint "https://<account>.services.ai.azure.com/api/projects/<project>" \
  --agent-name travel-request-agent
```

## Cosmos DB のコンテナーとキーを固定する

| コンテナー | パーティションキー | 用途 |
|---|---|---|
| `workflow-checkpoints` | `/workflow_name` | Agent Framework のチェックポイント |
| `conversation-events` | `/conversation_id` | durable SSE イベント |
| `conversations` | `/id` | 所有者、Responses ID、pending HITL |
| `approval-grants` | `/id` | 送信承認の短命 grant |
| `travel-requests` | `/request_id` | 冪等に登録した申請 |
| `evaluation-cases` | `/dataset_id` | 評価入力、期待値、タグ、版 |
| `evaluation-runs` | `/id` | Datasetと2つのFoundry run、Agent version、集計 |
| `evaluation-results` | `/comparison_id` | ケース単位の両出力、自動評価、人手評価 |

`workflow-checkpoints` は30日で削除します。`approval-grants` はコンテナー側で TTL を有効にし、各アイテムには発行時の短い TTL を設定します。

## 運用で見る場所を分ける

- **Application Insights**: Foundry Hosted Agent のサーバー側トレースと、BFF / Functions の警告・エラー
- **Foundry**: Prompt AgentとHosted Agentのversion、Dataset、Evaluation run、トレース、モデル利用状況
- **Cosmos DB**: pending HITL、イベント再生、grantの消費状態、申請の重複有無、比較結果
- **Container Apps / Functions**: Easy Auth、Managed Identity、revision と実行ログ

Foundry プロジェクトは Project Managed Identity を使って Application Insights に接続します。プロジェクトの Managed Identity にはトレースの送信・参照に必要なロールを割り当てます。Hosted Agentの再デプロイ後はホスティング基盤がGenAIトレースを自動送信します。プロンプト本文の記録、OpenTelemetryのログ、メトリクス、SDK統計は有効化せず、取り込み量を抑えます。

Foundryでは、HITLの確認・修正・承認を応答ターンごとのトレースとして記録します。BFFは同じ会話IDを`agent_session_id`として引き継ぐため、Foundryの「セッションビュー」から1回の申請に含まれるトレースをまとめて確認できます。

`scripts/configure-hosted-agent.sh`はHosted Agentのデプロイ後に実行します。AgentにはApplication Insightsの`Monitoring Metrics Publisher`、Cosmos DB Built-in Data Contributor、Foundry Agent Consumer、Functions Easy Authの`allowedApplications`を設定します。BFFとFoundryプロジェクトのManaged Identityには、Datasetとバッチ評価を実行するためのFoundryロールを割り当てます。

## 用語

| 用語 | このリポジトリでの意味 |
|---|---|
| BFF | Browser からの API 呼び出しを受け、認証とバックエンド連携を集約する FastAPI |
| Hosted Agent | Foundry がコンテナーを管理し、Responses API として公開する Agent Framework アプリ |
| Prompt Agent | モデル、instructions、toolsをFoundry側のversionとして管理するエージェント |
| HITL | 依頼内容、旅程、送信を利用者が確認してから処理を再開する仕組み |
| approval grant | BFF が送信承認時だけ発行し、MCP が副作用の直前に検証する短命な許可情報 |
| durable SSE | イベントをメモリではなく Cosmos DB に保存し、切断後も `Last-Event-ID` から再送する方式 |
| comparison run | 同じDatasetと評価基準で、2つのエージェント構成を対にして実行した評価単位 |

実装上の問題や改善案は、このリポジトリの GitHub Issues に登録してください。
