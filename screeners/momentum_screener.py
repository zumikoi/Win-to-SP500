# =============================================================================
#  US Stock Momentum Screener  →  Google Sheets
#  使い方: python -m screeners.momentum_screener   (リポジトリルートで実行)
#          Cloud Run Job としてスケジュール実行される。ローカル実行も同じコマンド。
#  出力: 「セクター別モメンタム」「銘柄別モメンタム」「サマリー_ランキング」
# =============================================================================
#  ※ 本スクリプトはスクリーニング/情報整理の道具です。投資助言ではありません。
# =============================================================================

# ------------------------- 0. セットアップ -------------------------
import sys
import warnings, time, datetime as dt
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

import gspread

from common import run_job, with_retry

JOB_NAME = "momentum-screener"

# ------------------------- 1. 設定 (ここだけ編集) -------------------------
SPREADSHEET_NAME = "モメンタムスクリーナー"        # 無ければ自動作成
BENCHMARK        = "SPY"                     # RSの基準

# --- セクターETF ---
#  GICS 11セクター(XL*)+ 短期モメンタムが立ちやすいテーマ/業種ETF
#  不要な行はコメントアウトすれば実行時間を短縮できます
SECTORS = {
    # ==== GICS 11セクター(ベースライン) ====
    "テクノロジー":       "XLK",
    "一般消費財":         "XLY",
    "通信サービス":       "XLC",
    "金融":               "XLF",
    "ヘルスケア":         "XLV",
    "資本財":             "XLI",
    "エネルギー":         "XLE",
    "素材":               "XLB",
    "生活必需品":         "XLP",
    "公益":               "XLU",
    "不動産":             "XLRE",

    # ==== テクノロジー細分化 ====
    "半導体":             "SMH",    # 大型中心
    "半導体(等ウェイト)": "SOXX",   # 中小型の動きを拾う。SMHとの乖離が中小型の物色度合いを示す
                                     # (構成銘柄がSMHとほぼ同じため、銘柄タブでは重複を避けSTOCKS未定義)
    "半導体製造装置":     "XSD",
    "ソフトウェア":       "IGV",
    "サイバーセキュリティ": "CIBR",
    "AI/ビッグデータ":    "AIQ",
    "データセンター/インフラ": "SRVR",
    "クラウド":           "SKYY",
    "インターネット":     "FDN",
    "ロボティクス/AI":    "BOTZ",
    "量子コンピューティング": "QTUM",
    "フィンテック":       "FINX",

    # ==== ヘルスケア細分化 ====
    "バイオテック":       "XBI",    # 中小型・金利感応度高
    "製薬":               "PPH",
    "医療機器":           "IHI",

    # ==== 金融細分化 ====
    #  (ユーザー方針により細分化なし。GICSの XLF のみで見る)

    # ==== 資本財/防衛 ====
    "航空宇宙・防衛":     "ITA",
    "輸送":               "IYT",
    "建設/ホームビルダー": "XHB",

    # ==== エネルギー/資源 ====
    "石油サービス":       "OIH",
    "原油生産(E&P)":     "XOP",
    "クリーンエネルギー": "ICLN",
    "ウラン/原子力":      "URA",
    "金鉱株":             "GDX",
    "銅/産業金属":        "COPX",

    # ==== 消費 ====
    "小売":               "XRT",
}

# --- 各セクターの代表銘柄(各5銘柄) ---
#  詳細な銘柄分析は別スクリーナーで行う前提。ここはセクターの中身を確認するための参照用。
#
#  ★ 同一銘柄は1セクターにのみ登場する(重複なし)。
#     競合した場合は「より細分化されたセクター」を優先している。
#     例: CRWD/PANW → サイバーセキュリティ(ソフトウェアからは除外)
#         NVDA      → 半導体(AI/ビッグデータからは除外)
#         AMZN      → インターネット(一般消費財・クラウドからは除外)
#     広いGICSセクターには、細分化セクターに吸われなかった銘柄が残る。
STOCKS = {
    # ==== GICS 11セクター(細分化に吸われなかった銘柄) ====
    "テクノロジー":       ["AAPL", "CRM", "ACN", "IBM", "TXN"],
    "一般消費財":         ["TSLA", "HD", "MCD", "BKNG", "SBUX"],
    "通信サービス":       ["NFLX", "DIS", "TMUS", "VZ", "T"],
    "金融":               ["BRK-B", "JPM", "GS", "BAC", "AXP"],
    "ヘルスケア":         ["UNH", "JNJ", "TMO", "CI", "HCA"],
    "資本財":             ["GE", "CAT", "UBER", "BA", "HON"],
    "エネルギー":         ["XOM", "CVX", "COP", "PSX", "MPC"],
    "素材":               ["LIN", "SHW", "APD", "ECL", "NUE"],
    "生活必需品":         ["PG", "KO", "PEP", "PM", "MDLZ"],
    "公益":               ["NEE", "SO", "DUK", "CEG", "VST"],
    "不動産":             ["PLD", "AMT", "WELL", "SPG", "O"],

    # ==== テクノロジー細分化 ====
    "半導体":             ["NVDA", "AMD", "TSM", "MU", "AVGO"],
    "半導体製造装置":     ["ASML", "AMAT", "LRCX", "KLAC", "TER"],
    "ソフトウェア":       ["NOW", "MSFT", "ORCL", "DDOG", "TEAM"],
    "サイバーセキュリティ": ["PANW", "CRWD", "FTNT", "ZS", "OKTA"],
    "AI/ビッグデータ":    ["PLTR", "SNOW", "AI", "MDB", "PATH"],
    "データセンター/インフラ": ["VRT", "SMCI", "EQIX", "DLR", "ANET"],
    "クラウド":           ["GOOGL", "NET", "TWLO", "ZM", "HUBS"],
    "インターネット":     ["AMZN", "META", "SHOP", "ABNB", "DASH"],
    "ロボティクス/AI":    ["ISRG", "ROK", "ABB", "SYM", "NVT"],
    "量子コンピューティング": ["IONQ", "RGTI", "QBTS", "QUBT", "ARQQ"],
    "フィンテック":       ["V", "MA", "PYPL", "COIN", "SOFI"],

    # ==== ヘルスケア細分化 ====
    "バイオテック":       ["AMGN", "GILD", "VRTX", "REGN", "MRNA"],
    "製薬":               ["LLY", "ABBV", "MRK", "PFE", "BMY"],
    "医療機器":           ["ABT", "MDT", "SYK", "BSX", "EW"],

    # ==== 金融細分化 ====
    #  (細分化なし。大手銀行/地方銀行/保険は「金融」に集約)

    # ==== 資本財/防衛 ====
    "航空宇宙・防衛":     ["RTX", "LMT", "NOC", "GD", "LHX"],
    "輸送":               ["UNP", "UPS", "FDX", "CSX", "ODFL"],
    "建設/ホームビルダー": ["DHI", "LEN", "PHM", "NVR", "BLDR"],

    # ==== エネルギー/資源 ====
    "石油サービス":       ["SLB", "HAL", "BKR", "FTI", "NOV"],
    "原油生産(E&P)":     ["EOG", "OXY", "DVN", "FANG", "CTRA"],
    "クリーンエネルギー": ["FSLR", "ENPH", "SEDG", "RUN", "PLUG"],
    "ウラン/原子力":      ["CCJ", "LEU", "UEC", "SMR", "OKLO"],
    "金鉱株":             ["NEM", "GOLD", "AEM", "FNV", "WPM"],
    "銅/産業金属":        ["FCX", "SCCO", "TECK", "BHP", "RIO"],

    # ==== 消費 ====
    "小売":               ["WMT", "COST", "TGT", "TJX", "ORLY"],
}

# 営業日ベースのルックバック
LB_1W, LB_1M, LB_3M = 5, 21, 63

FETCH_EARNINGS = False  # 決算ドリフト取得。銘柄分析は別スクリーナーで行う前提のためデフォルトOFF
                        # (Trueにすると5〜10分余計にかかる)
CHUNK_SIZE     = 60     # yfinance 一括取得のチャンクサイズ(レート制限回避)

# --- ユニバースの健全性チェック(編集ミスの早期検出) ---
from collections import Counter as _Counter

def check_universe():
    _flat = [t for v in STOCKS.values() for t in v]
    _dup  = {t: n for t, n in _Counter(_flat).items() if n > 1}
    if _dup:
        _where = {t: [s for s, v in STOCKS.items() if t in v] for t in _dup}
        raise ValueError(f"STOCKSに重複ティッカーがあります(1銘柄1セクターにしてください): {_where}")
    if set(STOCKS) - set(SECTORS):
        raise ValueError(f"SECTORSに存在しないSTOCKSキー: {set(STOCKS) - set(SECTORS)}")
    print(f"ユニバース: {len(SECTORS)} セクターETF / {len(_flat)} 銘柄(重複なし)")

# ------------------------- 2. データ取得 -------------------------
#  取得結果はモジュールグローバル(CLOSE/VOL/OPEN/HIGH/available)に展開する。
#  後続の指標計算関数がこれらを直接参照するため。
CLOSE = VOL = OPEN = HIGH = None
available = []

def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def fetch_market_data():
    global CLOSE, VOL, OPEN, HIGH, available

    all_tickers = sorted(set([BENCHMARK] + list(SECTORS.values()) +
                             [s for v in STOCKS.values() for s in v]))
    print(f"取得中: {len(all_tickers)} ティッカー (ユニーク, 重複除去済み) ...")

    frames = {"Close": [], "Volume": [], "Open": [], "High": []}
    for i, batch in enumerate(chunked(all_tickers, CHUNK_SIZE), 1):
        n_chunks = -(-len(all_tickers) // CHUNK_SIZE)
        print(f"  [{i}/{n_chunks}] {len(batch)} 銘柄 ...")
        r = with_retry(f"yfinance一括取得 [{i}/{n_chunks}]",
                       yf.download, batch, period="1y", interval="1d",
                       auto_adjust=True, progress=False, group_by="column",
                       threads=True,
                       validate=lambda d: d is not None and not d.empty)
        if len(batch) == 1:                      # 単一銘柄はMultiIndexにならない
            r.columns = pd.MultiIndex.from_product([r.columns, batch])
        for k in frames:
            if k in r.columns.get_level_values(0):
                frames[k].append(r[k])
        if i < n_chunks:
            time.sleep(1.5)                      # レート制限回避

    CLOSE = pd.concat(frames["Close"],  axis=1).dropna(how="all")
    VOL   = pd.concat(frames["Volume"], axis=1).dropna(how="all")
    OPEN  = pd.concat(frames["Open"],   axis=1).dropna(how="all")
    HIGH  = pd.concat(frames["High"],   axis=1).dropna(how="all")

    # 同一ティッカーが複数セクターに登場する場合の重複列を除去
    for _df in (CLOSE, VOL, OPEN, HIGH):
        _df.drop(columns=_df.columns[_df.columns.duplicated()], inplace=True)

    available = [t for t in all_tickers if t in CLOSE.columns and CLOSE[t].notna().sum() > 70]
    missing   = sorted(set(all_tickers) - set(available))
    if missing:
        print(f"⚠ データ不足でスキップ: {missing}")
    if BENCHMARK not in available:
        raise RuntimeError(f"ベンチマーク {BENCHMARK} のデータが取得できません。再実行してください。")
    # 大半が欠けている場合はレート制限/ブロックの疑い。空の結果で上書きしないよう中断する。
    if len(available) < len(all_tickers) * 0.5:
        raise RuntimeError(
            f"株価取得の成功が {len(available)}/{len(all_tickers)} ティッカーのみ。"
            "レート制限/ブロックの疑いがあるため中断します。")

# ------------------------- 3. 指標計算 -------------------------
def pct_change_n(s: pd.Series, n: int):
    s = s.dropna()
    if len(s) <= n:
        return np.nan
    return s.iloc[-1] / s.iloc[-1 - n] - 1

def rsi_wilder(s: pd.Series, period: int = 14):
    s = s.dropna()
    if len(s) < period + 1:
        return np.nan
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    last_loss = loss.iloc[-1]
    if last_loss == 0:
        return 100.0
    rs = gain.iloc[-1] / last_loss
    return 100 - 100 / (1 + rs)

def cross_status(ma20: pd.Series, ma50: pd.Series, lookback: int = 5):
    """直近 lookback 営業日以内のGC/DC発生を検出"""
    v = (ma20 - ma50).dropna()
    if len(v) < lookback + 1:
        return "判定不可"
    now, past = v.iloc[-1], v.iloc[-1 - lookback]
    if now > 0 and past <= 0:
        return "GC発生(直近5日)"
    if now < 0 and past >= 0:
        return "DC発生(直近5日)"
    return "20>50(強気配列)" if now > 0 else "20<50(弱気配列)"

_earn_cache = {}
def earnings_drift(sym: str):
    """直近決算の翌営業日始値 → 現在値のリターン。(ドリフト, 決算日)"""
    if not FETCH_EARNINGS:
        return np.nan, ""
    if sym in _earn_cache:
        return _earn_cache[sym]
    result = (np.nan, "")
    try:
        ed = yf.Ticker(sym).get_earnings_dates(limit=12)
        if ed is not None and len(ed):
            idx = pd.to_datetime(ed.index).tz_localize(None)
            past = idx[idx < pd.Timestamp.now()]
            if len(past):
                edate = past.max()
                px = CLOSE[sym].dropna()
                after = OPEN[sym].dropna().loc[OPEN[sym].dropna().index > edate]
                # 決算から45営業日以内のみ有効(それ以降はドリフトとして扱わない)
                if len(after) and len(after) <= 60:
                    result = (px.iloc[-1] / after.iloc[0] - 1, edate.strftime("%Y-%m-%d"))
                elif len(after):
                    result = (np.nan, edate.strftime("%Y-%m-%d"))
    except Exception:
        pass
    _earn_cache[sym] = result
    time.sleep(0.15)   # レート制限回避
    return result

def asof_slice(s: pd.Series, asof: int) -> pd.Series:
    """asof営業日前まで遡ったシリーズを返す(asof=0で最新)"""
    s = s.dropna()
    return s.iloc[:len(s) - asof] if asof > 0 else s

def bench_returns(asof: int):
    b = asof_slice(CLOSE[BENCHMARK], asof)
    return pct_change_n(b, LB_1M), pct_change_n(b, LB_3M)

def build_row(sym: str, label: str, asof: int = 0, with_earnings: bool = False):
    """asof営業日前の時点で見た場合の各指標を返す"""
    c = asof_slice(CLOSE[sym], asof)
    v = asof_slice(VOL[sym], asof)
    h = asof_slice(HIGH[sym], asof)
    if len(c) < LB_3M + 55:          # MA50 + 3ヶ月分に足りない
        return None

    price = c.iloc[-1]
    b1m, b3m = bench_returns(asof)

    r1w = pct_change_n(c, LB_1W)
    r1m = pct_change_n(c, LB_1M)
    r3m = pct_change_n(c, LB_3M)

    hi52 = h.tail(252).max()
    dev52 = price / hi52 - 1 if pd.notna(hi52) and hi52 > 0 else np.nan

    rs1m = (1 + r1m) / (1 + b1m) - 1 if pd.notna(r1m) and pd.notna(b1m) else np.nan
    rs3m = (1 + r3m) / (1 + b3m) - 1 if pd.notna(r3m) and pd.notna(b3m) else np.nan

    v5, v20 = v.tail(5).mean(), v.tail(20).mean()
    vol_ratio = v5 / v20 if v20 > 0 else np.nan

    ma20_s, ma50_s = c.rolling(20).mean(), c.rolling(50).mean()
    ma20, ma50 = ma20_s.iloc[-1], ma50_s.iloc[-1]

    if price > ma20 and price > ma50:   ma_pos = "両線の上"
    elif price > ma20:                  ma_pos = "20上/50下"
    elif price > ma50:                  ma_pos = "20下/50上"
    else:                               ma_pos = "両線の下"

    rsi = rsi_wilder(c)
    drift, edate = earnings_drift(sym) if with_earnings else (np.nan, "")

    return {
        "ラベル": label, "ティッカー": sym, "現在値": round(price, 2),
        "1週間騰落率": r1w, "1ヶ月騰落率": r1m, "3ヶ月騰落率": r3m,
        "52週高値乖離": dev52, "RS_1ヶ月": rs1m, "RS_3ヶ月": rs3m,
        "出来高比_5d/20d": vol_ratio,
        "MA20": round(ma20, 2), "MA50": round(ma50, 2),
        "MA位置": ma_pos, "GC/DC": cross_status(ma20_s, ma50_s),
        "RSI14": rsi, "決算日": edate, "決算ドリフト": drift,
    }

# ------------------- 3b. 3時点のスナップショットを構築 -------------------
#  同じロジックを asof=0 / 5 / 21 で回し、当時の判定を復元する
SNAPSHOTS = {"現在": 0, "1週間前": LB_1W, "1ヶ月前": LB_1M}

def build_frame(mapping, is_stock: bool, asof: int):
    """mapping: {ラベル: ティッカー or [ティッカー]} → DataFrame"""
    rows = []
    if is_stock:
        for name, syms in mapping.items():
            for s in syms:
                if s in available:
                    # 決算情報は現在時点のみ取得(過去に遡っての取得は不可)
                    r = build_row(s, name, asof, with_earnings=(asof == 0))
                    if r: rows.append(r)
    else:
        for name, t in mapping.items():
            if t in available:
                r = build_row(t, name, asof, with_earnings=False)
                if r: rows.append(r)
    key = "銘柄" if is_stock else "ETF"
    df = pd.DataFrame(rows).rename(columns={"ラベル": "セクター", "ティッカー": key})
    if not is_stock:
        df = df.drop(columns=["決算日", "決算ドリフト"])
    return df

# ------------------------- 4. モメンタムスコア(0-100) -------------------------
def momentum_score(df: pd.DataFrame) -> pd.Series:
    """相対順位(スナップショット内パーセンタイル)ベース。RS重視・トレンド構造と出来高で裏取り。

    ※ 決算ドリフトはスコアに含めない。過去時点に遡って再現できないため、
       含めると「現在の判定」と「1週間前の判定」が別ロジックになり比較不能になる。
       決算ドリフトは表示専用の列として残す。
    """
    pr = lambda col: df[col].rank(pct=True, na_option="bottom")
    score = (
        30 * pr("RS_1ヶ月")                                        # 短期モメンタムの核
        + 20 * pr("RS_3ヶ月")                                      # トレンドの持続性
        + 15 * pr("1週間騰落率")                                    # 直近の加速
        + 10 * (df["現在値"] > df["MA20"]).astype(int)              # 短期トレンド健在
        + 10 * (df["MA20"] > df["MA50"]).astype(int)                # 強気配列
        + 10 * ((df["出来高比_5d/20d"] > 1.2) & (df["1週間騰落率"] > 0)).astype(int)  # 資金流入を伴う上昇
        +  5 * df["RSI14"].between(50, 70).astype(int)              # 強いが過熱でない
    )
    return score.round(0).astype(int)

def classify(row) -> str:
    s, p, ma20, ma50, rsi, dev = (row["スコア"], row["現在値"], row["MA20"],
                                  row["MA50"], row["RSI14"], row["52週高値乖離"])
    if pd.notna(dev) and dev < -0.20:
        return "失速/売り検討"                      # 高値から-20%超は強制降格
    if s >= 70 and p > ma20:
        return "加速中(買い継続)"
    if s >= 50 and p > ma50:
        return "継続中"
    if (s >= 50 and p < ma50) or (30 <= s < 50) or (pd.notna(rsi) and rsi > 80):
        return "転換注意"
    return "失速/売り検討"

def scored(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["スコア"] = momentum_score(df)
    df["判定"]   = df.apply(classify, axis=1)
    return df

# --- 3時点それぞれでスコア・判定を算出 ---
ORDER = ["加速中(買い継続)", "継続中", "転換注意", "失速/売り検討"]
RANK  = {v: i for i, v in enumerate(ORDER)}        # 0が最強

def transition(now: str, past: str) -> str:
    if pd.isna(past) or past == "":
        return "―"                                 # 当時データ不足
    if now == past:
        return "変化なし"
    arrow = "▲改善" if RANK[now] < RANK[past] else "▼悪化"
    short = lambda x: x.split("(")[0]
    return f"{arrow}: {short(past)}→{short(now)}"

def attach_history(mapping, is_stock: bool) -> pd.DataFrame:
    key = "銘柄" if is_stock else "ETF"
    base = None
    for label, asof in SNAPSHOTS.items():
        print(f"  スナップショット計算: {label} (asof={asof}営業日前)")
        snap = scored(build_frame(mapping, is_stock, asof))
        if label == "現在":
            base = snap
        else:
            # 同一ティッカーが複数セクターに出るため、セクター+ティッカーで結合
            add = snap[["セクター", key, "スコア", "判定"]].rename(
                columns={"スコア": f"スコア_{label}", "判定": f"判定_{label}"})
            base = base.merge(add, on=["セクター", key], how="left")

    for label in ("1週間前", "1ヶ月前"):
        base[f"判定変化({label}比)"] = base.apply(
            lambda r: transition(r["判定"], r.get(f"判定_{label}")), axis=1)
        # 履歴が無い行(新規上場など)は NaN のまま → シート上は空欄になる
        base[f"スコア変化({label}比)"] = base["スコア"] - base[f"スコア_{label}"]
        base[f"判定_{label}"] = base[f"判定_{label}"].fillna("―")
    return base

def main(gc, sh):
    """モメンタム計算〜シート出力の本体。gc/sh は common.run_job から渡される。
    戻り値は実行ステータスタブに残すサマリー文字列。"""
    check_universe()
    fetch_market_data()

    print("セクター指標を計算中...")
    sec = attach_history(SECTORS, is_stock=False)
    sec["ETF資金フロー($M)"] = ""     # ← yfinance非対応。etf.com等から週次で手入力

    print("銘柄指標を計算中(決算情報の取得に時間がかかります)...")
    stk = attach_history(STOCKS, is_stock=True)

    # 列順を整える(スコア/判定と、その変化をB列の右に集約)
    HIST_COLS = ["判定_1週間前", "判定_1ヶ月前",
                 "判定変化(1週間前比)", "判定変化(1ヶ月前比)",
                 "スコア変化(1週間前比)", "スコア変化(1ヶ月前比)"]

    sec = sec[["セクター", "ETF", "スコア", "判定"] + HIST_COLS +
              ["現在値", "1週間騰落率", "1ヶ月騰落率", "3ヶ月騰落率",
               "52週高値乖離", "RS_1ヶ月", "RS_3ヶ月", "出来高比_5d/20d",
               "MA20", "MA50", "MA位置", "GC/DC", "RSI14",
               "スコア_1週間前", "スコア_1ヶ月前",
               "ETF資金フロー($M)"]].sort_values("スコア", ascending=False)

    stk = stk[["セクター", "銘柄", "スコア", "判定"] + HIST_COLS +
              ["現在値", "1週間騰落率", "1ヶ月騰落率", "3ヶ月騰落率",
               "52週高値乖離", "RS_1ヶ月", "RS_3ヶ月", "出来高比_5d/20d",
               "MA20", "MA50", "MA位置", "GC/DC", "RSI14",
               "スコア_1週間前", "スコア_1ヶ月前",
               "決算日", "決算ドリフト"]].sort_values("スコア", ascending=False)

    # ------------------------- 5. サマリー -------------------------
    counts = sec["判定"].value_counts().reindex(ORDER).fillna(0).astype(int)

    # セクター数に依存しないよう「加速中の比率」で地合いを判定
    accel = int(counts["加速中(買い継続)"])
    accel_pct = accel / len(sec) if len(sec) else 0
    regime = ("リスクオン: 加速セクターが厚い" if accel_pct >= 0.30 else
              "中立: 選別局面" if accel_pct >= 0.15 else
              "リスクオフ警戒: 加速セクターが乏しい。キャッシュ比率の引き上げを検討")

    # 1週間前の地合いとの比較(加速中セクター数が増えているか減っているか)
    accel_1w = int((sec["判定_1週間前"] == "加速中(買い継続)").sum())
    breadth = ("拡大中(モメンタムの裾野が広がっている)" if accel > accel_1w else
               "横ばい" if accel == accel_1w else
               "縮小中(モメンタムが一部銘柄に集中しつつある)")

    # 判定変化の集計
    def change_counts(df):
        up   = int(df["判定変化(1週間前比)"].str.startswith("▲").sum())
        down = int(df["判定変化(1週間前比)"].str.startswith("▼").sum())
        return up, down

    sec_up, sec_down = change_counts(sec)
    stk_up, stk_down = change_counts(stk)

    # 直近1週間で格上げ/格下げされた銘柄(注目リスト)
    upgraded   = stk[stk["判定変化(1週間前比)"].str.startswith("▲")].head(15)[
        ["銘柄", "セクター", "スコア", "判定変化(1週間前比)"]]
    downgraded = stk[stk["判定変化(1週間前比)"].str.startswith("▼")].head(15)[
        ["銘柄", "セクター", "スコア", "判定変化(1週間前比)"]]

    top10  = stk.head(10)[["銘柄", "セクター", "スコア", "判定", "判定変化(1週間前比)"]]
    worst10 = stk.tail(10).sort_values("スコア")[["銘柄", "セクター", "スコア", "判定", "判定変化(1週間前比)"]]

    # ------------------------- 6. Google Sheets 出力 -------------------------
    #  認証とスプレッドシートのオープンは common.run_job 側で済んでいる
    #  (アタッチされたサービスアカウントを google.auth.default() で使用)。

    def get_ws(title, rows=200, cols=26):
        try:
            ws = sh.worksheet(title)
            ws.clear()
            # 既存シートが小さいと列が切れるので必要なら拡張
            if ws.row_count < rows or ws.col_count < cols:
                ws.resize(rows=max(ws.row_count, rows), cols=max(ws.col_count, cols))
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=rows, cols=cols)
        return ws

    def to_values(df):
        d = df.copy()
        for c in d.columns:
            if d[c].dtype.kind in "fc":
                d[c] = d[c].astype(object).where(d[c].notna(), "")
        return [d.columns.tolist()] + d.values.tolist()

    ws_sec = get_ws("セクター別モメンタム", rows=len(sec) + 20, cols=len(sec.columns) + 2)
    ws_stk = get_ws("銘柄別モメンタム",   rows=len(stk) + 20, cols=len(stk.columns) + 2)
    ws_sum = get_ws("サマリー_ランキング", rows=len(sec) + 60, cols=26)

    ws_sec.update(range_name="A1", values=to_values(sec), value_input_option="RAW")
    ws_stk.update(range_name="A1", values=to_values(stk), value_input_option="RAW")

    ws_sum.update(range_name="A1", values=[
        ["■ 地合いサマリー"],
        ["更新日時(JST)", (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")],
        ["加速中セクター数", f"{accel} / {len(sec)} ({accel_pct:.0%})"],
        ["  1週間前", f"{accel_1w} / {len(sec)}"],
        ["モメンタムの裾野", breadth],
        ["地合い判定", regime],
    ], value_input_option="RAW")

    ws_sum.update(range_name="A8", values=[["■ 判定別カウント(セクター)"]] +
                        [[k, int(v)] for k, v in counts.items()], value_input_option="RAW")

    ws_sum.update(range_name="A14", values=[
        ["■ 判定変化サマリー(1週間前比)"],
        ["", "▲改善", "▼悪化"],
        ["セクター", sec_up, sec_down],
        ["銘柄",     stk_up, stk_down],
    ], value_input_option="RAW")

    ws_sum.update(range_name="A20", values=[["■ セクターランキング(スコア降順)"]] +
                         to_values(sec[["セクター", "ETF", "スコア", "判定",
                                        "判定変化(1週間前比)", "RS_1ヶ月"]]),
                  value_input_option="RAW")

    ws_sum.update(range_name="H20", values=[["■ 銘柄TOP10"]] + to_values(top10), value_input_option="RAW")
    ws_sum.update(range_name="N20", values=[["■ 銘柄WORST10"]] + to_values(worst10), value_input_option="RAW")

    # 直近1週間で判定が動いた銘柄 = 最も注目すべきリスト
    ws_sum.update(range_name="H33", values=[["■ 格上げ(▲改善) 直近1週間"]] +
                         (to_values(upgraded) if len(upgraded) else [["該当なし"]]),
                  value_input_option="RAW")
    ws_sum.update(range_name="N33", values=[["■ 格下げ(▼悪化) 直近1週間"]] +
                         (to_values(downgraded) if len(downgraded) else [["該当なし"]]),
                  value_input_option="RAW")

    # ------------------------- 7. 書式・条件付き書式 -------------------------
    GREEN_D = {"red": 0.204, "green": 0.659, "blue": 0.325}
    GREEN_L = {"red": 0.718, "green": 0.882, "blue": 0.804}
    YELLOW  = {"red": 0.988, "green": 0.910, "blue": 0.698}
    RED_L   = {"red": 0.957, "green": 0.780, "blue": 0.765}
    RED_D   = {"red": 0.647, "green": 0.055, "blue": 0.055}
    WHITE   = {"red": 1, "green": 1, "blue": 1}
    ORANGE  = {"red": 0.965, "green": 0.698, "blue": 0.420}
    BLUE_L  = {"red": 0.643, "green": 0.761, "blue": 0.957}
    PURPLE  = {"red": 0.851, "green": 0.824, "blue": 0.914}

    def col_idx(df, name):
        return df.columns.get_loc(name)

    def col_letter(idx: int) -> str:
        """0→A, 25→Z, 26→AA (26列超でも壊れないように)"""
        s = ""
        idx += 1
        while idx:
            idx, r = divmod(idx - 1, 26)
            s = chr(65 + r) + s
        return s

    def num_fmt(ws, df, name, fmt_type, pattern):
        i = col_idx(df, name)
        n = len(df) + 1
        return {"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": n,
                      "startColumnIndex": i, "endColumnIndex": i + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": fmt_type, "pattern": pattern}}},
            "fields": "userEnteredFormat.numberFormat"}}

    def text_rule(ws, df, name, contains, bg, fg=None):
        i = col_idx(df, name)
        n = len(df) + 1
        fmt = {"backgroundColor": bg}
        if fg:
            fmt["textFormat"] = {"foregroundColor": fg, "bold": True}
        return {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": n,
                        "startColumnIndex": i, "endColumnIndex": i + 1}],
            "booleanRule": {"condition": {"type": "TEXT_CONTAINS",
                                          "values": [{"userEnteredValue": contains}]},
                            "format": fmt}}, "index": 0}}

    def gradient_rule(ws, df, name):
        i = col_idx(df, name)
        n = len(df) + 1
        return {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": n,
                        "startColumnIndex": i, "endColumnIndex": i + 1}],
            "gradientRule": {
                "minpoint": {"color": {"red": 0.902, "green": 0.486, "blue": 0.451}, "type": "MIN"},
                "midpoint": {"color": WHITE, "type": "NUMBER", "value": "0"},
                "maxpoint": {"color": {"red": 0.341, "green": 0.733, "blue": 0.541}, "type": "MAX"}}},
            "index": 0}}

    def custom_rule(ws, df, name, formula, bg):
        i = col_idx(df, name)
        n = len(df) + 1
        return {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": n,
                        "startColumnIndex": i, "endColumnIndex": i + 1}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                          "values": [{"userEnteredValue": formula}]},
                            "format": {"backgroundColor": bg}}}, "index": 0}}

    def header_fmt(ws, ncols):
        return [
            {"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                          "startColumnIndex": 0, "endColumnIndex": ncols},
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.16, "green": 0.16, "blue": 0.16},
                    "textFormat": {"foregroundColor": WHITE, "bold": True},
                    "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
            {"updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"}}]

    def build_requests(ws, df, letter_col_for_rsi="RSI14"):
        reqs = header_fmt(ws, len(df.columns))

        # 数値書式 —— スコアは必ず整数(パーセント表示バグ防止)
        for c in ["1週間騰落率", "1ヶ月騰落率", "3ヶ月騰落率", "52週高値乖離",
                  "RS_1ヶ月", "RS_3ヶ月", "決算ドリフト"]:
            if c in df.columns:
                reqs.append(num_fmt(ws, df, c, "PERCENT", "0.0%"))
        for c in ["現在値", "MA20", "MA50", "出来高比_5d/20d"]:
            if c in df.columns:
                reqs.append(num_fmt(ws, df, c, "NUMBER", "0.00"))
        reqs.append(num_fmt(ws, df, "RSI14", "NUMBER", "0.0"))
        for c in ["スコア", "スコア_1週間前", "スコア_1ヶ月前"]:
            reqs.append(num_fmt(ws, df, c, "NUMBER", "0"))
        # スコア変化は符号付き整数(+3 / -5 のように表示)
        for c in ["スコア変化(1週間前比)", "スコア変化(1ヶ月前比)"]:
            reqs.append(num_fmt(ws, df, c, "NUMBER", "+0;-0;0"))

        # 判定列の色分け(現在・過去とも同じ配色)
        for c in ["判定", "判定_1週間前", "判定_1ヶ月前"]:
            reqs += [text_rule(ws, df, c, "加速中",   GREEN_D, WHITE),
                     text_rule(ws, df, c, "継続中",   GREEN_L),
                     text_rule(ws, df, c, "転換注意", YELLOW),
                     text_rule(ws, df, c, "失速",     RED_L, RED_D)]

        # 判定変化列: 改善=緑 / 悪化=赤 / 変化なし=無着色
        for c in ["判定変化(1週間前比)", "判定変化(1ヶ月前比)"]:
            reqs += [text_rule(ws, df, c, "▲", GREEN_D, WHITE),
                     text_rule(ws, df, c, "▼", RED_L, RED_D)]

        # スコア変化のカラースケール
        for c in ["スコア変化(1週間前比)", "スコア変化(1ヶ月前比)"]:
            reqs.append(gradient_rule(ws, df, c))

        # GC/DC
        reqs += [text_rule(ws, df, "GC/DC", "GC発生", GREEN_L),
                 text_rule(ws, df, "GC/DC", "DC発生", RED_L)]

        # 騰落率・RSのカラースケール
        for c in ["1週間騰落率", "1ヶ月騰落率", "3ヶ月騰落率", "RS_1ヶ月", "RS_3ヶ月"]:
            reqs.append(gradient_rule(ws, df, c))

        # RSI: 80超=過熱 / 30未満=モメンタム消失
        ri = col_letter(col_idx(df, "RSI14"))
        reqs.append(custom_rule(ws, df, "RSI14", f"=${ri}2>=80", ORANGE))
        reqs.append(custom_rule(ws, df, "RSI14", f"=${ri}2<=30", BLUE_L))

        # 出来高比 1.5超 = 異常出来高(ニュース要確認)
        vi = col_letter(col_idx(df, "出来高比_5d/20d"))
        reqs.append(custom_rule(ws, df, "出来高比_5d/20d", f"=${vi}2>=1.5", PURPLE))

        # 先頭4列(セクター/ティッカー/スコア/判定)を固定して横スクロールしやすく
        reqs.append({"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "gridProperties": {"frozenColumnCount": 4}},
            "fields": "gridProperties.frozenColumnCount"}})

        return reqs

    requests = build_requests(ws_sec, sec) + build_requests(ws_stk, stk)
    sh.batch_update({"requests": requests})

    # 列幅の自動調整(各タブの実列数+余白まで対象)
    sh.batch_update({"requests": [
        {"autoResizeDimensions": {"dimensions": {
            "sheetId": ws_sec.id, "dimension": "COLUMNS",
            "startIndex": 0, "endIndex": len(sec.columns) + 2}}},
        {"autoResizeDimensions": {"dimensions": {
            "sheetId": ws_stk.id, "dimension": "COLUMNS",
            "startIndex": 0, "endIndex": len(stk.columns) + 2}}},
        {"autoResizeDimensions": {"dimensions": {
            "sheetId": ws_sum.id, "dimension": "COLUMNS",
            "startIndex": 0, "endIndex": 22}}},
    ]})

    # ------------------------- 8. 完了 -------------------------
    print("\n" + "=" * 60)
    print(f"✅ 完了: {sh.url}")
    print("=" * 60)
    print(f"地合い判定 : {regime}")
    print(f"加速中セクター数: {accel} / {len(sec)} ({accel_pct:.0%})  ← 1週間前: {accel_1w}")
    print(f"モメンタムの裾野: {breadth}")
    print(f"対象: {len(sec)} セクター / {len(stk)} 銘柄")
    print(f"\n判定変化(1週間前比)  セクター: ▲{sec_up} / ▼{sec_down}   銘柄: ▲{stk_up} / ▼{stk_down}")

    print("\n■ セクター上位10")
    print(sec.head(10)[["セクター", "ETF", "スコア", "判定", "判定変化(1週間前比)"]].to_string(index=False))

    print("\n■ 直近1週間で格上げ(▲改善)された銘柄")
    print(upgraded.to_string(index=False) if len(upgraded) else "  該当なし")

    print("\n■ 直近1週間で格下げ(▼悪化)された銘柄")
    print(downgraded.to_string(index=False) if len(downgraded) else "  該当なし")
    print("\n■ 銘柄TOP10")
    print(top10.to_string(index=False))
    print("\n■ 銘柄WORST10")
    print(worst10.to_string(index=False))
    print("\n※ ETF資金フロー列は手入力(etf.com / VettaFi等)。決算5営業日前の銘柄は判定に関わらず縮小/見送りを原則とする。")

    return (f"{len(sec)}セクター / {len(stk)}銘柄 / 加速中:{accel}({accel_pct:.0%})"
            f" / {regime}")


if __name__ == "__main__":
    sys.exit(run_job(JOB_NAME, SPREADSHEET_NAME, main))
