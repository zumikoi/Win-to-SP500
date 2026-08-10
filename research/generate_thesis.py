"""
generate_thesis.py
-------------------
Claude Portfolio(@theaiportfolios)方式の「AIリサーチ + 人間が最終判断」ワークフローを
個人用に再現するモジュール。

役割分担（The Claude Portfolioの運営者インタビューに準拠）:
  - ルール（ポジションサイズ上限・入れ替え頻度など） → 人間が事前に決める
  - 個別銘柄の強気/弱気/ベースシナリオ、反証可能なテーゼ → Claudeが生成
  - 最終的な売買判断・発注 → 人間（あなた）が行う

minervini_screener.py が「抽出結果」タブに書き出したショートリスト
(トレンドテンプレート8/8通過 + ファンダ足切り通過の銘柄)に対して回す。

実行方法:
  python -m research.generate_thesis   (リポジトリルートで実行)
  Cloud Run Job として週2回スケジュール実行される。

必要な環境:
  - ANTHROPIC_API_KEY: Secret Manager から環境変数として注入される
  - Google認証: Cloud Run Job にアタッチされたサービスアカウント
    (ローカルで試す場合は `gcloud auth application-default login`)
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import anthropic

# ============================================================
# 設定
# ============================================================

# 分析の質を重視するならopus、コストを抑えるならsonnetに変更
MODEL_NAME = os.environ.get("THESIS_MODEL", "claude-sonnet-5")

# 週2回など、レビュー頻度は自分で決めて呼び出し側でコントロールする
# （毎日回すとノイズを拾うだけなので非推奨）

# --- ショートリストの取得元 / 出力先 ---
SPREADSHEET_NAME = os.environ.get("THESIS_SPREADSHEET", "ミネルヴィニ銘柄スクリーナー")
SOURCE_SHEET_NAME = os.environ.get("THESIS_SOURCE_SHEET", "抽出結果")
THESIS_SHEET_NAME = os.environ.get("THESIS_SHEET", "投資テーゼ")

# 1回の実行でテーゼを生成する最大銘柄数。「抽出結果」タブは
# 判定(買い場の近さ)→総合スコア降順で並んでいるため、上から順に採用する。
# スクリーナー側の順位付けをそのまま使い、ここで独自の優先順位は付けない。
MAX_TICKERS = int(os.environ.get("THESIS_MAX_TICKERS", "15"))

# トレンドテンプレートは「抽出結果」に載っている時点で8条件すべて通過している
# (minervini_screener.py が `if not all(tt): continue` で落としているため)。
TREND_TEMPLATE_TOTAL = 8

JOB_NAME = "thesis-generator"

THESIS_HEADER = ["日時", "銘柄", "強気シナリオ", "弱気シナリオ", "ベースシナリオ",
                 "テーゼ", "無効化条件", "確信度"]

THESIS_SYSTEM_PROMPT = """あなたは個人投資家のリサーチアシスタントです。
与えられた銘柄データについて、Minervini SEPA / CAN SLIMの文脈を踏まえた
定性分析を行ってください。

出力は必ず以下のJSON形式のみとし、前後に説明文やMarkdownのコードフェンスを
一切付けないでください。JSON以外の文字を含めると処理が失敗します。

{
  "bull_case": "強気シナリオを2-3文で",
  "bear_case": "弱気シナリオを2-3文で",
  "base_case": "最も可能性が高いシナリオを2-3文で",
  "thesis": "この銘柄を保有/監視する理由を1文で明確に",
  "invalidation_condition": "このテーゼが崩れたと判断する具体的な条件(数値や事象で)",
  "confidence": "low/mid/highのいずれか"
}
"""


def _build_user_prompt(ticker: str, data_summary: str) -> str:
    return f"""銘柄: {ticker}

以下はあなたのスクリーニングシステムから得られたデータです:
{data_summary}

このデータをもとに、上記フォーマットのJSONを生成してください。
根拠のない楽観・悲観は避け、データに基づいた記述にしてください。"""


def _strip_json_fence(text: str) -> str:
    """Claudeがまれに付けるコードフェンスを除去"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned.replace("json", "", 1).strip()
    return cleaned


def generate_thesis(client: anthropic.Anthropic, ticker: str, data_summary: str,
                     max_retries: int = 2) -> dict:
    """
    1銘柄分のテーゼをClaude APIで生成する。

    Parameters
    ----------
    client : anthropic.Anthropic
        初期化済みのAPIクライアント
    ticker : str
        銘柄コード (例: "ZETA")
    data_summary : str
        既存のtrend template / VCP / RSレーティングなどの結果を
        テキストで要約したもの。既存コードの出力をそのまま渡せばOK。
        例: "RS Rating: 92, VCP: Stage B, Trend Template: 8/8 pass,
             52週高値まで-4.2%, 出来高は50日平均比+180%"
    max_retries : int
        JSON parse失敗時の再試行回数

    Returns
    -------
    dict : bull_case, bear_case, base_case, thesis, invalidation_condition,
           confidence, ticker, generated_at を含む辞書
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=MODEL_NAME,
                max_tokens=1000,
                system=THESIS_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": _build_user_prompt(ticker, data_summary)}
                ],
            )
            raw_text = response.content[0].text
            cleaned = _strip_json_fence(raw_text)
            parsed = json.loads(cleaned)

            parsed["ticker"] = ticker
            parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
            return parsed

        except (json.JSONDecodeError, IndexError, KeyError) as e:
            last_error = e
            time.sleep(1)
            continue

    # 最終的に失敗した場合は例外を投げずエラー情報を返す(バッチ処理を止めないため)
    return {
        "ticker": ticker,
        "error": str(last_error),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def append_thesis_to_sheet(worksheet, thesis: dict) -> None:
    """
    gspreadのworksheetオブジェクトに1行追記する。
    シートのヘッダーは事前に以下の順で作成しておく想定:
    日時 | 銘柄 | 強気シナリオ | 弱気シナリオ | ベースシナリオ | テーゼ | 無効化条件 | 確信度
    """
    if "error" in thesis:
        worksheet.append_row([
            thesis["generated_at"], thesis["ticker"],
            "ERROR", str(thesis["error"]), "", "", "", ""
        ])
        return

    worksheet.append_row([
        thesis["generated_at"],
        thesis["ticker"],
        thesis.get("bull_case", ""),
        thesis.get("bear_case", ""),
        thesis.get("base_case", ""),
        thesis.get("thesis", ""),
        thesis.get("invalidation_condition", ""),
        thesis.get("confidence", ""),
    ])


def run_thesis_batch(client: anthropic.Anthropic, shortlist: list, worksheet,
                      data_summary_fn, sleep_sec: float = 1.5) -> list:
    """
    ショートリスト全体に対してテーゼ生成→シート書き込みを行う。

    Parameters
    ----------
    shortlist : list[str]
        トレンドテンプレート/VCPを通過した銘柄コードのリスト
    worksheet : gspread.Worksheet
        書き込み先シート
    data_summary_fn : callable
        ticker(str) -> data_summary(str) を返す関数。
        既存のsepa_toolkit_v2.py側の分析結果をここに接続する。
    sleep_sec : float
        APIレート制限対策の待機秒数

    Returns
    -------
    list[dict] : 生成した全テーゼ
    """
    results = []
    for ticker in shortlist:
        try:
            data_summary = data_summary_fn(ticker)
            thesis = generate_thesis(client, ticker, data_summary)
            append_thesis_to_sheet(worksheet, thesis)
            status = "OK" if "error" not in thesis else f"ERROR: {thesis['error']}"
            print(f"  {ticker}: {status}")
            results.append(thesis)
        except Exception as e:
            print(f"  {ticker}: FAILED - {e}")
        time.sleep(sleep_sec)

    return results


# ============================================================
# minervini_screener.py のショートリストとの接続
# ============================================================

# 「抽出結果」タブのヘッダー名 → data_summary に載せるラベル。
# スクリーナー側でヘッダーを変えた場合はここも合わせる。
SUMMARY_FIELDS = [
    ("判定", "買い場判定"),
    ("ステージ", "ステージ"),
    ("相対強度", "RS Rating"),
    ("VCP", "VCP Grade"),
    ("VCPスコア", "VCPスコア"),
    ("形", "ベースの形"),
    ("総合スコア", "総合スコア"),
    ("ファンダ点", "ファンダ点"),
    ("EPS成長Q(前年比%)", "EPS成長(前年同期比)"),
    ("売上成長Q(前年比%)", "売上成長(前年同期比)"),
    ("ROE(%)", "ROE"),
    ("利益率(%)", "利益率"),
    ("ファンダ根拠", "ファンダ根拠"),
    ("現在値($)", "現在値"),
    ("買いポイント($)", "買いポイント"),
    ("乖離率(%)", "買いポイントからの乖離"),
    ("50日線乖離率(%)", "50日線からの乖離"),
    ("トレンド強度(%)", "200日線からの乖離"),
    ("逆指値目安($)", "逆指値目安"),
    ("企業名", "企業名"),
    ("業態", "業態"),
]

_TICKER_IN_FORMULA = re.compile(r"t=([A-Z0-9.\-]+)")
# ティッカーとして妥当な形だけ通す。スクリーナーは該当0件のとき
# 「本日は全条件を満たす銘柄なし」という文言をこの列に書くため、
# それを銘柄として拾わないようにする。
_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _clean_ticker(raw: str) -> str:
    """ティッカー列は =HYPERLINK(...) 式。表示値なら素のティッカー、
    式のまま取れた場合は URL から抜き出す。ティッカーに見えなければ空文字。"""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("="):
        m = _TICKER_IN_FORMULA.search(s)
        s = m.group(1) if m else ""
    s = s.upper()
    return s if _TICKER_PATTERN.match(s) else ""


def load_shortlist(sh, source_sheet_name: str = SOURCE_SHEET_NAME,
                   limit: int = MAX_TICKERS):
    """「抽出結果」タブを読み、(ショートリスト, {ticker: 行dict}) を返す。

    シートは判定→総合スコア降順に並んでいるので、その順序をそのまま維持し、
    上から limit 件を採用する。ここで並べ替えや足切りは行わない。
    """
    ws = sh.worksheet(source_sheet_name)
    values = ws.get_all_values()
    if len(values) < 2:
        return [], {}

    headers = [h.strip() for h in values[0]]
    try:
        ticker_col = headers.index("ティッカー(Finvizへ)")
    except ValueError:
        raise RuntimeError(
            f"「{source_sheet_name}」タブにティッカー列が見つかりません。"
            f"検出したヘッダー: {headers[:6]}")

    shortlist, rows = [], {}
    for raw_row in values[1:]:
        if len(raw_row) <= ticker_col:
            continue
        ticker = _clean_ticker(raw_row[ticker_col])
        if not ticker or ticker in rows:
            continue
        rows[ticker] = {h: (raw_row[i] if i < len(raw_row) else "")
                        for i, h in enumerate(headers)}
        shortlist.append(ticker)
        if len(shortlist) >= limit:
            break
    return shortlist, rows


def make_data_summary_fn(rows: dict):
    """{ticker: 行dict} から data_summary_fn を作る。
    スクリーナーの判定結果を、そのままの数値でテキスト化するだけ
    (再計算も再解釈もしない)。"""
    def data_summary_fn(ticker: str) -> str:
        row = rows.get(ticker, {})
        parts = [f"Trend Template: {TREND_TEMPLATE_TOTAL}/{TREND_TEMPLATE_TOTAL} pass "
                 f"(このリストに載っている時点で全条件通過済み)"]
        for key, label in SUMMARY_FIELDS:
            value = str(row.get(key, "")).strip()
            if value:
                parts.append(f"{label}: {value}")
        return "\n".join(parts)
    return data_summary_fn


def get_thesis_worksheet(sh, title: str = THESIS_SHEET_NAME):
    """「投資テーゼ」タブを取得(無ければヘッダー付きで作成)。
    既存の行は消さず、時系列で追記していく。"""
    import gspread

    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title, rows=1000, cols=len(THESIS_HEADER))
        ws.update(range_name="A1", values=[THESIS_HEADER], value_input_option="RAW")
        ws.freeze(rows=1)
        return ws

    if not (ws.acell("A1").value or "").strip():
        ws.update(range_name="A1", values=[THESIS_HEADER], value_input_option="RAW")
    return ws


# ============================================================
# Cloud Run Job エントリポイント
# ============================================================

def main(gc, sh) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "環境変数 ANTHROPIC_API_KEY が空です。Cloud Run Job の "
            "--set-secrets=ANTHROPIC_API_KEY=anthropic-api-key:latest を確認してください。")
    client = anthropic.Anthropic(api_key=api_key)

    print(f"1/3 「{SOURCE_SHEET_NAME}」タブからショートリストを読み込み中...")
    shortlist, rows = load_shortlist(sh)
    if not shortlist:
        print("  ショートリストが空でした(本日は該当銘柄なし)。テーゼ生成をスキップします。")
        return "対象銘柄なし(ショートリストが空)"
    print(f"  対象: {len(shortlist)}銘柄 → {', '.join(shortlist)}")

    print(f"2/3 「{THESIS_SHEET_NAME}」タブを準備中...")
    ws = get_thesis_worksheet(sh)

    print(f"3/3 テーゼを生成中(model={MODEL_NAME})...")
    results = run_thesis_batch(client, shortlist, ws, make_data_summary_fn(rows))

    n_ok = sum(1 for r in results if "error" not in r)
    n_ng = len(results) - n_ok
    print(f"【完了】{n_ok}銘柄のテーゼを生成しました(失敗: {n_ng}銘柄)")
    return f"{n_ok}銘柄生成 / 失敗{n_ng}銘柄 / 対象{len(shortlist)}銘柄"


if __name__ == "__main__":
    from common import run_job

    sys.exit(run_job(JOB_NAME, SPREADSHEET_NAME, main))
