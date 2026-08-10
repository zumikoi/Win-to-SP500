"""
common.py — Cloud Run Jobs 上で動かすための共通ユーティリティ

Colab固有の認証(google.colab.auth)を置き換え、外部データ取得の
リトライ/バックオフと、実行結果のステータス記録を提供する。

投資判断に関わるロジック(スコアリング・フィルタ条件・閾値)は
このファイルには一切含まれない。実行環境まわりの面倒だけを引き受ける。
"""

import datetime
import os
import time
import traceback

# Google Sheets / Drive にアクセスするためのスコープ。
# Cloud Run Job にアタッチされたサービスアカウントの認証情報が
# google.auth.default() 経由で自動的に使われる(JSON鍵は一切使わない)。
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 外部データ取得(Finviz / yfinance)のリトライ設定。
# GCPのデータセンターIPはレート制限に遭いやすいため、失敗したら
# 既定で30秒待って1回だけ再試行する。環境変数で調整可能。
RETRY_COUNT = int(os.environ.get("RETRY_COUNT", "1"))
RETRY_WAIT_SEC = float(os.environ.get("RETRY_WAIT_SEC", "30"))

STATUS_SHEET_NAME = "実行ステータス"
STATUS_KEEP_ROWS = 50

JST = datetime.timezone(datetime.timedelta(hours=9))


def now_jst_str() -> str:
    return datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")


# ============================================================
# 認証
# ============================================================

def authorize_gspread():
    """アタッチされたサービスアカウント(ローカルでは application-default 認証情報)で
    gspread クライアントを作る。JSON鍵ファイルは読み込まない。

    ローカルで試す場合は事前に:
        gcloud auth application-default login \
          --scopes=https://www.googleapis.com/auth/spreadsheets,\
https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform
    """
    import google.auth
    import gspread

    creds, _ = google.auth.default(scopes=SCOPES)
    return gspread.authorize(creds)


def open_spreadsheet(gc, name: str):
    """名前でスプレッドシートを開く。無ければ作る(初回のみ)。"""
    import gspread

    try:
        return gc.open(name)
    except gspread.SpreadsheetNotFound:
        sh = gc.create(name)
        print(f"  「{name}」を新規作成しました(サービスアカウントのマイドライブ直下)")
        return sh


# ============================================================
# リトライ
# ============================================================

def with_retry(label, fn, *args, validate=None, **kwargs):
    """fn(*args, **kwargs) を実行し、失敗したら待ってから再試行する。

    validate: 戻り値を受け取って True/False を返す関数。False なら失敗扱い。
              yfinance や finvizfinance は「例外を投げずに空の結果を返す」ことが
              あるため、その静かな失敗を拾うのに使う。
    """
    last_error = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            result = fn(*args, **kwargs)
            if validate is not None and not validate(result):
                raise RuntimeError("応答が空、または想定外の形式でした")
            return result
        except Exception as e:  # noqa: BLE001 - 外部I/Oの失敗は種類を問わず再試行する
            last_error = e
            if attempt < RETRY_COUNT:
                wait = RETRY_WAIT_SEC * (attempt + 1)
                print(f"  [retry] {label} に失敗 ({type(e).__name__}: {str(e)[:100]})"
                      f" → {wait:.0f}秒待って再試行 ({attempt + 1}/{RETRY_COUNT})")
                time.sleep(wait)
            else:
                print(f"  [error] {label} に失敗(リトライ上限に到達): "
                      f"{type(e).__name__}: {str(e)[:200]}")
    raise last_error


# ============================================================
# 実行ステータスの記録(サイレント失敗の防止)
# ============================================================

def record_status(sh, job_name: str, status: str, detail: str = "") -> None:
    """スプレッドシートの「実行ステータス」タブに1行(新しい順)記録する。

    既存の抽出結果タブを壊さないよう、専用タブに書く。失敗しても本処理を
    止めないよう例外は握りつぶす(ログには出す)。
    """
    import gspread

    header = ["実行日時(JST)", "ジョブ", "ステータス", "詳細"]
    try:
        try:
            ws = sh.worksheet(STATUS_SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(STATUS_SHEET_NAME, rows=STATUS_KEEP_ROWS + 10, cols=4)
            ws.update(range_name="A1", values=[header], value_input_option="RAW")

        if not (ws.acell("A1").value or "").strip():
            ws.update(range_name="A1", values=[header], value_input_option="RAW")

        ws.insert_row([now_jst_str(), job_name, status, detail[:1000]],
                      index=2, value_input_option="RAW")

        # 古い行を間引く(シートが無限に伸びないように)
        if ws.row_count > STATUS_KEEP_ROWS + 1:
            ws.delete_rows(STATUS_KEEP_ROWS + 2, ws.row_count)
    except Exception as e:  # noqa: BLE001 - ステータス記録の失敗で本処理を落とさない
        print(f"  [warn] 実行ステータスの記録に失敗: {type(e).__name__}: {str(e)[:120]}")


def run_job(job_name: str, spreadsheet_name: str, body) -> int:
    """ジョブ本体を実行し、結果を「実行ステータス」タブに必ず残す。

    body(gc, sh) -> str(サマリー文字列) を受け取る。
    例外が出た場合は「取得失敗」を記録した上で終了コード1を返す。
    戻り値をそのまま sys.exit() に渡す想定。
    """
    gc = authorize_gspread()
    sh = open_spreadsheet(gc, spreadsheet_name)
    try:
        summary = body(gc, sh)
        record_status(sh, job_name, "成功", summary or "")
        return 0
    except Exception as e:  # noqa: BLE001 - 失敗をシートに残してから終了する
        traceback.print_exc()
        detail = f"{type(e).__name__}: {e}"
        print(f"【失敗】{job_name}: {detail}")
        record_status(sh, job_name, "取得失敗", detail)
        return 1
