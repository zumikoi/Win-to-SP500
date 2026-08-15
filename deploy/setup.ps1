<#
    setup.ps1 — Windows(PowerShell)から直接セットアップするスクリプト

    使い方(PowerShellで、このリポジトリのフォルダに移動してから):
        powershell -ExecutionPolicy Bypass -File ".\deploy\setup.ps1"

    コードを直したあと、デプロイだけやり直す場合:
        powershell -ExecutionPolicy Bypass -File ".\deploy\setup.ps1" -DeployOnly

    あなたが手を動かすのは2か所だけ:
      (1) Anthropic APIキーの貼り付け
      (2) スプレッドシート2つの共有(画面が一時停止します)

    何度実行しても壊れません(作成済みのものはスキップされます)。

    ※ このファイルは「UTF-8(BOM付き)」で保存してあります。
       Windows PowerShell 5.1 が日本語を正しく読むために必要です。
#>

[CmdletBinding()]
param(
    [switch]$DeployOnly,
    [string]$Region = "asia-northeast1"
)

# gcloud は進捗を標準エラー出力に書くため、Stop にすると正常動作でも例外になる。
# 成否は $LASTEXITCODE で明示的に判定する。
$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

# リポジトリのルート(このスクリプトの1つ上)。
# フォルダ名に空白が含まれていても壊れないよう、必ず変数と -LiteralPath で扱う。
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$SaId           = "screener-runner"
$SecretName     = "anthropic-api-key"
$SheetMinervini = "ミネルヴィニ銘柄スクリーナー"
$SheetMomentum  = "モメンタムスクリーナー"

# ---------------------------------------------------------------
# 表示用ヘルパー
# ---------------------------------------------------------------
function Write-Step($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}
function Write-Ok($text)   { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Note($text) { Write-Host "  $text"      -ForegroundColor DarkGray }
function Write-Warn($text) { Write-Host "  [!] $text"  -ForegroundColor Yellow }

function Stop-WithError($text) {
    Write-Host ""
    Write-Host "[失敗] $text" -ForegroundColor Red
    Write-Host ""
    Write-Note "上の赤い文字をそのままコピーして相談してください。"
    Write-Note "直したあと、同じコマンドをもう一度実行すれば続きから進みます。"
    Write-Host ""
    exit 1
}

# gcloud は外部コマンドなので、終了コードを自分で確認する
function Invoke-GCloud {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$FailMessage = "gcloud コマンドが失敗しました",
        [switch]$AllowFailure,
        [switch]$Silent
    )
    if ($Silent) {
        & gcloud @Arguments *> $null
    } else {
        & gcloud @Arguments
    }
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        Stop-WithError $FailMessage
    }
}

# 「すでに存在するか」の確認用。存在すれば $true。
function Test-GCloudResource {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & gcloud @Arguments *> $null
    return ($LASTEXITCODE -eq 0)
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor White
Write-Host "  スクリーナー自動化 セットアップ (Windows)"           -ForegroundColor White
Write-Host "=====================================================" -ForegroundColor White
Write-Note "作業フォルダ: $Root"

# ---------------------------------------------------------------
Write-Step "1/8  Google Cloud CLI の確認"
# ---------------------------------------------------------------
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "[失敗] gcloud(Google Cloud CLI)が見つかりません。" -ForegroundColor Red
    Write-Host ""
    Write-Host "  まず下記からインストールしてください(5分ほど):" -ForegroundColor White
    Write-Host "    https://cloud.google.com/sdk/docs/install#windows" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  インストーラを最後まで進めたあと、" -ForegroundColor White
    Write-Host "  【PowerShellをいったん閉じて、開き直してから】やり直してください。" -ForegroundColor Yellow
    Write-Note "(開き直さないと、インストール済みでも『見つからない』ままです)"
    Write-Host ""
    exit 1
}
Write-Ok "gcloud を検出しました"

$account = (& gcloud config get-value account) 2>$null
if ([string]::IsNullOrWhiteSpace($account) -or $account -eq "(unset)") {
    Write-Warn "ログインしていません。ブラウザが開くので、いつものGoogleアカウントでログインしてください。"
    Invoke-GCloud -Arguments @("auth", "login") -FailMessage "ログインに失敗しました。"
    $account = (& gcloud config get-value account) 2>$null
}
Write-Ok "ログイン中のアカウント: $account"

$projectId = (& gcloud config get-value project) 2>$null
if ([string]::IsNullOrWhiteSpace($projectId) -or $projectId -eq "(unset)") {
    Write-Host ""
    Write-Host "  使えるプロジェクトの一覧:" -ForegroundColor White
    & gcloud projects list --format="table(projectId, name)"
    Write-Host ""
    $projectId = Read-Host "  使うプロジェクトIDを入力してEnter"
    if ([string]::IsNullOrWhiteSpace($projectId)) { Stop-WithError "プロジェクトIDが空です。" }
    Invoke-GCloud -Arguments @("config", "set", "project", $projectId) -Silent `
        -FailMessage "プロジェクトの設定に失敗しました。IDが正しいか確認してください。"
}
Write-Ok "プロジェクト: $projectId"
Write-Ok "リージョン: $Region"

$serviceAccount = "$SaId@$projectId.iam.gserviceaccount.com"
$schedulerSa    = "scheduler-invoker@$projectId.iam.gserviceaccount.com"

if (-not $DeployOnly) {

    # ---------------------------------------------------------------
    Write-Step "2/8  必要なAPIを有効化(2〜3分かかります)"
    # ---------------------------------------------------------------
    Write-Note "Cloud Run / Scheduler / Secret Manager / Sheets / Drive / Cloud Build"
    Invoke-GCloud -Arguments @(
        "services", "enable",
        "run.googleapis.com", "cloudscheduler.googleapis.com",
        "secretmanager.googleapis.com", "sheets.googleapis.com",
        "drive.googleapis.com", "cloudbuild.googleapis.com",
        "--project=$projectId"
    ) -FailMessage "APIの有効化に失敗しました。プロジェクトで『お支払い(請求先)』が有効か確認してください: https://console.cloud.google.com/billing/linkedaccount?project=$projectId"
    Write-Ok "APIを有効化しました"

    # ---------------------------------------------------------------
    Write-Step "3/8  実行用サービスアカウントの作成"
    # ---------------------------------------------------------------
    if (Test-GCloudResource @("iam", "service-accounts", "describe", $serviceAccount, "--project=$projectId")) {
        Write-Ok "作成済みです: $serviceAccount"
    } else {
        Invoke-GCloud -Arguments @(
            "iam", "service-accounts", "create", $SaId,
            "--project=$projectId", "--display-name=Screener Job Runner"
        ) -Silent -FailMessage "サービスアカウントの作成に失敗しました。"
        Write-Ok "作成しました: $serviceAccount"
    }

    Invoke-GCloud -Arguments @(
        "projects", "add-iam-policy-binding", $projectId,
        "--member=serviceAccount:$serviceAccount",
        "--role=roles/secretmanager.secretAccessor",
        "--condition=None"
    ) -Silent -AllowFailure
    Write-Ok "Secret Manager の読み取り権限を付与しました"
    Write-Note "※ このサービスアカウントに鍵ファイル(JSON)は作りません。"

    # ---------------------------------------------------------------
    Write-Step "4/8  Anthropic APIキーの登録"
    # ---------------------------------------------------------------
    $secretExists = Test-GCloudResource @("secrets", "describe", $SecretName, "--project=$projectId")

    $doRegister = $true
    if ($secretExists) {
        Write-Ok "登録済みです(secret名: $SecretName)"
        $replace = Read-Host "  新しいキーに入れ替えますか? [y/N]"
        $doRegister = ($replace -match '^[Yy]$')
    } else {
        Write-Note "Anthropic のコンソール( https://console.anthropic.com/settings/keys )で"
        Write-Note "発行したキー( sk-ant- で始まる文字列 )を貼り付けてください。"
        Write-Host ""
    }

    if ($doRegister) {
        $secure = Read-Host "  APIキーを貼り付けてEnter (画面には表示されません)" -AsSecureString
        $bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        if ([string]::IsNullOrWhiteSpace($apiKey)) { Stop-WithError "APIキーが空です。" }

        # 末尾に改行が混ざるとキーが壊れるため、改行なしで一時ファイルに書いて渡し、直後に消す
        $keyFile = Join-Path ([System.IO.Path]::GetTempPath()) ("k_" + [Guid]::NewGuid().ToString("N"))
        try {
            [System.IO.File]::WriteAllText($keyFile, $apiKey.Trim(),
                                           (New-Object System.Text.UTF8Encoding($false)))
            if ($secretExists) {
                Invoke-GCloud -Arguments @("secrets", "versions", "add", $SecretName,
                    "--project=$projectId", "--data-file=$keyFile") -Silent `
                    -FailMessage "APIキーの更新に失敗しました。"
                Write-Ok "更新しました"
            } else {
                Invoke-GCloud -Arguments @("secrets", "create", $SecretName,
                    "--project=$projectId", "--data-file=$keyFile") -Silent `
                    -FailMessage "APIキーの登録に失敗しました。"
                Write-Ok "登録しました(secret名: $SecretName)"
            }
        } finally {
            if (Test-Path -LiteralPath $keyFile) { Remove-Item -LiteralPath $keyFile -Force }
            $apiKey = $null
        }
    }

    # ---------------------------------------------------------------
    Write-Step "5/8  【あなたの作業】スプレッドシート2つの共有"
    # ---------------------------------------------------------------
    Write-Host ""
    Write-Host "  下のメールアドレスを、スプレッドシート2つの共有相手に追加してください。" -ForegroundColor White
    Write-Host ""
    Write-Host "      $serviceAccount" -ForegroundColor Green
    Write-Host ""
    Write-Host "  対象のスプレッドシート:" -ForegroundColor White
    Write-Host "      ・$SheetMinervini"
    Write-Host "      ・$SheetMomentum"
    Write-Host ""
    Write-Host "  手順(2つとも同じ):" -ForegroundColor White
    Write-Host "      1. スプレッドシートを開く"
    Write-Host "      2. 右上の「共有」ボタンをクリック"
    Write-Host "      3. 上の入力欄に、上記のメールアドレスを貼り付ける"
    Write-Host "      4. 右側の権限を「編集者」に変更する   ← 閲覧者のままだと動きません" -ForegroundColor Yellow
    Write-Host "      5. 「通知を送信」のチェックを外す      ← 外さないとエラーになることがあります" -ForegroundColor Yellow
    Write-Host "      6. 「送信」(または「共有」)をクリック"
    Write-Host ""
    Write-Warn "これを忘れると、エラーにならずに『空の別シート』が作られ、"
    Write-Warn "あなたのシートは永久に更新されません。一番大事な手順です。"
    Write-Host ""
    Read-Host "  2つとも共有できたらEnterを押してください" | Out-Null

    # ---------------------------------------------------------------
    Write-Step "6/8  設定ファイルの作成"
    # ---------------------------------------------------------------
    # Cloud Shell(bash)側からも同じ設定で操作できるように書き出しておく。
    # bash が読むファイルなので、改行コードは LF に固定する。
    $configPath = Join-Path $Root "deploy\config.sh"
    $configText = @"
# deploy/setup.ps1 が自動生成しました($(Get-Date -Format 'yyyy-MM-dd HH:mm'))
PROJECT_ID="$projectId"
SERVICE_ACCOUNT="$serviceAccount"
REGION="$Region"
SCHEDULER_SA="$schedulerSa"
ANTHROPIC_SECRET_NAME="$SecretName"

# スケジュール(UTC)。JSTに直すには9時間足す。
MINERVINI_SCHEDULE="30 23 * * 1-5"   # JST 平日の翌朝 08:30
MOMENTUM_SCHEDULE="45 23 * * 1-5"    # JST 平日の翌朝 08:45
THESIS_SCHEDULE="0 0 * * 2,5"        # JST 火・金の 09:00

THESIS_MAX_TICKERS="15"
"@
    [System.IO.File]::WriteAllText($configPath, ($configText -replace "`r`n", "`n"),
                                   (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "deploy\config.sh を作成しました"
}

# ---------------------------------------------------------------
Write-Step "7/8  デプロイ(初回は10分ほど。放置してOK)"
# ---------------------------------------------------------------
function Deploy-Job {
    param(
        [string]$Name,
        [string]$Module,
        [string]$Timeout,
        [string]$Memory,
        [string[]]$Extra = @()
    )
    Write-Host ""
    Write-Host "  --- $Name をデプロイ中 (python -m $Module) ---" -ForegroundColor White
    $gcArgs = @(
        "run", "jobs", "deploy", $Name,
        "--project=$projectId",
        "--source=.",
        "--region=$Region",
        "--service-account=$serviceAccount",
        "--task-timeout=$Timeout",
        "--memory=$Memory",
        "--max-retries=1",
        "--command=python",
        "--args=-m,$Module",
        "--quiet"
    ) + $Extra
    Invoke-GCloud -Arguments $gcArgs -FailMessage "$Name のデプロイに失敗しました。"
}

Deploy-Job -Name "minervini-screener" -Module "screeners.minervini_screener" -Timeout "900"  -Memory "1Gi"
Deploy-Job -Name "momentum-screener"  -Module "screeners.momentum_screener"  -Timeout "1800" -Memory "2Gi"
Deploy-Job -Name "thesis-generator"   -Module "research.generate_thesis"     -Timeout "1800" -Memory "1Gi" `
    -Extra @("--set-secrets=ANTHROPIC_API_KEY=${SecretName}:latest",
             "--set-env-vars=THESIS_MAX_TICKERS=15")
Write-Ok "3つのジョブをデプロイしました"

# ---------------------------------------------------------------
Write-Step "8/8  自動実行スケジュールの設定"
# ---------------------------------------------------------------
if (-not (Test-GCloudResource @("iam", "service-accounts", "describe", $schedulerSa, "--project=$projectId"))) {
    Invoke-GCloud -Arguments @(
        "iam", "service-accounts", "create", "scheduler-invoker",
        "--project=$projectId", "--display-name=Cloud Scheduler Job Invoker"
    ) -Silent -FailMessage "Scheduler用サービスアカウントの作成に失敗しました。"
}
Write-Ok "Scheduler用サービスアカウント: $schedulerSa"

foreach ($job in @("minervini-screener", "momentum-screener", "thesis-generator")) {
    Invoke-GCloud -Arguments @(
        "run", "jobs", "add-iam-policy-binding", $job,
        "--project=$projectId", "--region=$Region",
        "--member=serviceAccount:$schedulerSa", "--role=roles/run.invoker", "--quiet"
    ) -Silent -FailMessage "$job への権限付与に失敗しました。"
}
Write-Ok "各ジョブに実行権限を付与しました"

function Set-Schedule {
    param([string]$Name, [string]$JobName, [string]$Cron, [string]$Label)
    $uri  = "https://$Region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$projectId/jobs/${JobName}:run"
    $verb = if (Test-GCloudResource @("scheduler", "jobs", "describe", $Name,
                                      "--project=$projectId", "--location=$Region")) { "update" } else { "create" }
    Invoke-GCloud -Arguments @(
        "scheduler", "jobs", $verb, "http", $Name,
        "--project=$projectId", "--location=$Region",
        "--schedule=$Cron", "--time-zone=UTC",
        "--uri=$uri", "--http-method=POST",
        "--oauth-service-account-email=$schedulerSa", "--quiet"
    ) -Silent -FailMessage "スケジュール $Name の設定に失敗しました。"
    Write-Ok $Label
}

Set-Schedule -Name "minervini-daily" -JobName "minervini-screener" -Cron "30 23 * * 1-5" -Label "平日 08:30(JST) ミネルヴィニ・スクリーナー"
Set-Schedule -Name "momentum-daily"  -JobName "momentum-screener"  -Cron "45 23 * * 1-5" -Label "平日 08:45(JST) モメンタム・スクリーナー"
Set-Schedule -Name "thesis-biweekly" -JobName "thesis-generator"   -Cron "0 0 * * 2,5"   -Label "火・金 09:00(JST) 投資テーゼ生成"

# ---------------------------------------------------------------
Write-Step "テスト実行(5分ほど)"
# ---------------------------------------------------------------
Write-Note "ミネルヴィニ・スクリーナーを1回だけ動かして、シートが更新されるか確かめます。"
& gcloud run jobs execute minervini-screener --project=$projectId --region=$Region --wait
$testOk = ($LASTEXITCODE -eq 0)

Write-Host ""
Write-Host "=====================================================" -ForegroundColor White
if ($testOk) {
    Write-Host "  セットアップ完了" -ForegroundColor Green
    Write-Host "=====================================================" -ForegroundColor White
    Write-Host ""
    Write-Host "  スプレッドシート「$SheetMinervini」を開いて、次の2つを確認してください。" -ForegroundColor White
    Write-Host ""
    Write-Host "    1. 「抽出結果」タブ       … 今日の日付で銘柄が並んでいるか"
    Write-Host "    2. 「実行ステータス」タブ … 一番上の行が「成功」になっているか"
    Write-Host ""
    Write-Warn "「抽出結果」タブが更新されていない場合は、手順5の共有ができていません。"
    Write-Note "共有をやり直してから、次を実行してください:"
    Write-Note "  gcloud run jobs execute minervini-screener --region=$Region --wait"
    Write-Host ""
    Write-Host "  ここから先、PCを開く必要はありません。" -ForegroundColor Green
    Write-Host "    ・平日 朝08:30   ミネルヴィニ・スクリーナー が自動更新"
    Write-Host "    ・平日 朝08:45   モメンタム・スクリーナー が自動更新"
    Write-Host "    ・火・金 朝09:00 投資テーゼ が追記"
    Write-Host ""
    Write-Note "残り2つのジョブも今すぐ試すなら:"
    Write-Note "  gcloud run jobs execute momentum-screener --region=$Region --wait"
    Write-Note "  gcloud run jobs execute thesis-generator  --region=$Region --wait"
    Write-Host ""
} else {
    Write-Host "  テスト実行が失敗しました" -ForegroundColor Red
    Write-Host "=====================================================" -ForegroundColor White
    Write-Host ""
    Write-Host "  デプロイ自体は終わっています。失敗の原因を見るには:" -ForegroundColor White
    Write-Host "    gcloud run jobs executions list --job=minervini-screener --region=$Region --limit=1" -ForegroundColor Cyan
    Write-Host ""
    Write-Note "上の出力をそのままコピーして相談してください。"
    Write-Host ""
}
