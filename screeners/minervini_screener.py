# ============================================================
# ミネルヴィニ・スクリーナー 完成版(テクニカル+ファンダ+加速+地合い)
#
# 流れ: 地合いチェック(S&P500/NASDAQが200日線上か)
#       → Finvizで一次選別 → yfinanceで株価まとめ取り
#       → 8条件+VCP+50日線乖離で精密判定(テクニカル)
#       → ファンダ指標を%に正規化して取得(Finviz優先/yfinance補完)
#       → 成長の加速(EPS/売上が四半期を追って伸びているか)を評価
#       → ファンダ総合スコアで並び替え・足切りし、スプレッドシートへ色付き出力
#
# 使い方: python -m screeners.minervini_screener   (リポジトリルートで実行)
#         Cloud Run Job としてスケジュール実行される。ローカル実行も同じコマンド。
# ============================================================

# ---- 設定(ここだけ自分用に変える) ----
EQUITY           = 50_000                       # 口座額(ドル)
SPREADSHEET_NAME = "ミネルヴィニ銘柄スクリーナー"  # 出力先スプレッドシート名
SHEET_NAME       = "抽出結果"                    # 出力先タブ名
MAX_DETAIL       = 150                          # 詳細判定する最大銘柄数

# ---- ファンダ判定の基準(ミネルヴィニ本来基準に近い設定。すべて%単位) ----
FUND_MIN_SCORE   = 50    # この総合スコア(0-100)未満はファンダ落ちとして除外
EPS_Q_STRONG     = 25.0  # 直近四半期EPS成長率(前年比%)これ以上で満点級
EPS_Y_STRONG     = 25.0  # 当年EPS成長率(%)
SALES_Q_STRONG   = 20.0  # 直近四半期売上成長率(前年比%)
ROE_STRONG       = 17.0  # ROE(%)

# ---- テクニカルの過熱判定 ----
MA50_EXTENDED_PCT = 25.0  # 50日線からこの%以上、上に離れたら「伸びすぎ」

# ---- 地合い(マーケット全体)フィルタ ----
#  ミネルヴィニは相場全体が下降局面では新規買いを控える(現金比率を上げる)。
#  主要指数が自身の200日線より上にあるかを確認し、結果をシート冒頭に表示する。
#  RESPECT_MARKET=True の場合、地合いが悪いと株数目安を自動で半分に落とす。
CHECK_MARKET      = True
RESPECT_MARKET    = True   # 地合い悪化時に株数目安を保守化するか

import datetime
import os
import sys

import numpy as np
import pandas as pd

from common import run_job, with_retry

# 要注目銘柄の書き出し先(TradingViewインポート用)。Colabの /content は
# Cloud Run上に存在しないため、環境変数で差し替えられるようにしておく。
ALERTS_OUTPUT_PATH = os.environ.get("ALERTS_OUTPUT_PATH", "/tmp/alerts_watchlist.txt")

JOB_NAME = "minervini-screener"

# ============================================================
# テクニカル判定ロジック(既存)
# ============================================================
# ============================================================

def atr_pct(df, n=14):
    """1日の値動きの平均幅を、終値に対する%で返す(銘柄ごとの荒さの物差し)。"""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean() / c * 100


def zigzag_atr(df, k=2.0, floor_pct=3.0):
    """値動きの山と谷を検出。反転とみなす幅を銘柄の荒さに自動で合わせる。"""
    a = atr_pct(df).bfill().values
    H, L = df["High"].values, df["Low"].values
    n = len(df)
    if n < 30:
        return []
    trend = 1 if H[min(20, n - 1)] >= H[0] else -1
    ext_i = int(np.argmax(H[:min(20, n)])) if trend == 1 else int(np.argmin(L[:min(20, n)]))
    piv = []
    for i in range(ext_i + 1, n):
        thr = max(k * a[i], floor_pct) / 100.0
        if trend == 1:
            if H[i] >= H[ext_i]:
                ext_i = i
            elif L[i] <= H[ext_i] * (1 - thr):
                piv.append((ext_i, "H", float(H[ext_i]))); trend, ext_i = -1, i
        else:
            if L[i] <= L[ext_i]:
                ext_i = i
            elif H[i] >= L[ext_i] * (1 + thr):
                piv.append((ext_i, "L", float(L[ext_i]))); trend, ext_i = 1, i
    piv.append((ext_i, "H" if trend == 1 else "L",
                float(H[ext_i]) if trend == 1 else float(L[ext_i])))
    return piv


def detect_vcp(df):
    """VCP(買い場の形)の検出。値動きの縮小が段階的に浅くなり、
    出来高が枯れてくる形を点数化する。returns dict(valid, grade, score, pivot, stop, footprint)"""
    out = dict(valid=False, grade="-", score=0, pivot=np.nan, stop=np.nan, footprint="")
    if df is None or len(df) < 260:
        return out
    win = df.iloc[-160:]
    piv = zigzag_atr(win)
    hs = [p for p in piv if p[1] == "H"]
    if not hs:
        return out

    baseH, contr = piv[0][2] if piv[0][1] == "H" else -1, []
    started = piv[0][1] == "H"
    for j in range(len(piv) - 1):
        ix, typ, px = piv[j]
        if typ != "H":
            continue
        if not started or px > baseH * 1.05:
            baseH, contr, started = px, [], True
        nix, ntyp, npx = piv[j + 1]
        if ntyp == "L":
            contr.append(dict(h_i=ix, h=px, l=npx, depth=(px - npx) / px * 100))
    if not contr:
        return out

    contr = [c for c in contr if c["depth"] >= 3.0] or contr[-1:]
    if len(contr) >= 2:
        jump = 0
        for i in range(1, len(contr)):
            if contr[i]["depth"] > contr[i - 1]["depth"] * 1.6 and contr[i]["depth"] >= 8.0:
                jump = i
        contr = contr[jump:]
    if not contr:
        return out

    depths = [round(c["depth"], 1) for c in contr]
    close = float(df["Close"].iloc[-1])
    ma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    hi52 = float(df["High"].rolling(252).max().iloc[-1])
    pivot, stop = contr[-1]["h"], contr[-1]["l"] * 0.995
    base_bars = len(win) - contr[0]["h_i"]

    ok_n = 2 <= len(depths) <= 6
    if len(depths) >= 2:
        viol = sum(1 for i in range(len(depths) - 1) if depths[i + 1] > depths[i] * 0.90)
        ok_ratio = viol <= 1 and depths[-1] < depths[0] * 0.75
    else:
        ok_ratio = False
    ok_first = depths[0] <= 40.0
    ok_final = depths[-1] <= 12.0
    ok_len = 12 <= base_bars <= 150
    ok_ma = close >= ma50 * 0.97
    ok_near = pivot >= hi52 * 0.75

    vol = win["Volume"].iloc[-base_bars:]
    half = max(len(vol) // 2, 1)
    vh_ok = vol.iloc[half:].mean() <= vol.iloc[:half].mean() * 0.90
    dry_ok = df["Volume"].iloc[-5:].mean() <= df["Volume"].rolling(50).mean().iloc[-1] * 0.80

    score = (25 * (ok_n and ok_ratio) + 15 * ok_final + 10 * (depths[-1] <= 7)
             + 10 * vh_ok + 10 * dry_ok + 10 * ok_near + 5 * (pivot >= hi52 * 0.85)
             + 5 * ok_ma + 5 * ok_len + 5 * ok_first)
    valid = ok_n and ok_ratio and ok_first and ok_final and ok_len and ok_ma and ok_near
    grade = "A" if valid and score >= 85 else ("B" if valid and score >= 70 else ("C" if valid else "-"))
    weeks = max(round(base_bars / 5), 1)
    out.update(valid=valid, grade=grade, score=int(score),
               pivot=round(pivot, 2), stop=round(stop, 2),
               footprint=f"{weeks}W {depths[0]:.0f}/{depths[-1]:.0f} {len(depths)}T")
    return out


def triage(close, pivot, vcp_valid, hi52=None, ma50=None):
    """買い場への近さで仕分け。
    ★追加: 50日線から+MA50_EXTENDED_PCT%以上、上に離れた銘柄は、
      買いポイント基準に関わらずEXTENDED(伸びすぎ・追わない)に落とす。
      ミネルヴィニは主要移動平均線から大きく乖離した局面を買い場としないため。"""
    if not pivot or (isinstance(pivot, float) and np.isnan(pivot)):
        return "形成中"
    # ★50日線からの乖離しすぎ判定(新高値更新中でも過熱なら追わない)
    if ma50 and not np.isnan(ma50) and close >= ma50 * (1 + MA50_EXTENDED_PCT / 100.0):
        return "EXTENDED"
    if hi52 and close >= hi52 * 0.999:
        return "BREAKOUT"
    if close > pivot * 1.05:
        return "EXTENDED"
    if close >= pivot * 0.97:
        return "ALERT"
    if close >= pivot * 0.92:
        return "WATCH"
    return "形成中"


def classify_stage(df):
    """ステージ1-4の判定。ステージ2=上昇期。2初期を特に拾う。"""
    c = df["Close"]
    ma50 = c.rolling(50).mean()
    ma150 = c.rolling(150).mean()
    ma200 = c.rolling(200).mean()
    if len(df) < 260 or np.isnan(ma200.iloc[-1]):
        return "-"
    slope = ma200.iloc[-1] / ma200.iloc[-22] - 1
    px = float(c.iloc[-1])
    hi52 = float(c.rolling(252).max().iloc[-1])
    lo52 = float(c.rolling(252).min().iloc[-1])
    pos52 = (px - lo52) / max(hi52 - lo52, 1e-9)
    up = px > ma150.iloc[-1] > ma200.iloc[-1] and px > ma50.iloc[-1]

    if slope > 0.005 and up:
        turned = None
        for k in range(1, min(200, len(ma200) - 22)):
            if ma200.iloc[-k] <= ma200.iloc[-k - 21]:
                turned = k
                break
        if turned is not None and turned <= 63:
            return "2初期"
        return "2"
    if slope < -0.005 and px < ma200.iloc[-1]:
        return "4"
    if abs(slope) <= 0.005:
        return "3" if pos52 >= 0.6 else "1"
    return "2" if slope > 0 else "4"


def weighted_return(close):
    """相対強度の素点: 直近3ヶ月を重視した12ヶ月リターン(IBD式)。"""
    c = float(close.iloc[-1])
    def ret(n):
        return c / float(close.iloc[-n - 1]) - 1 if len(close) > n else np.nan
    parts = [0.4 * ret(63), 0.2 * ret(126), 0.2 * ret(189), 0.2 * ret(252)]
    return np.nansum(parts) if not all(np.isnan(p) for p in parts) else np.nan


# ============================================================
# ファンダメンタル判定ロジック(%正規化ベース)
# ============================================================

def _to_pct(x, is_ratio=False):
    """あらゆる形式を『%の数値』に統一する。
      is_ratio=False: '25.30%' / '25.3' / 25.3 → 25.3 (すでに%表記)
      is_ratio=True : 0.253 (小数の比率) → 25.3 (100倍して%化)
    取れなければ np.nan。"""
    if x is None:
        return np.nan
    if isinstance(x, (int, float)):
        v = float(x)
        if isinstance(x, float) and np.isnan(x):
            return np.nan
        return v * 100.0 if is_ratio else v
    s = str(x).strip().replace(',', '')
    had_pct = '%' in s
    s = s.replace('%', '')
    if s in ('', '-', 'nan', 'None', 'N/A'):
        return np.nan
    try:
        v = float(s)
    except ValueError:
        return np.nan
    # 文字列に%が付いていれば必ず%表記とみなす(is_ratioより優先)
    if had_pct:
        return v
    return v * 100.0 if is_ratio else v


def fund_from_finviz(row):
    """Finvizのfinancial/valuationビュー由来の1行(Series)からファンダ指標を%で抜き出す。
    Finvizの成長率・ROE・利益率はすべて『%付き文字列』なので is_ratio=False。"""
    def pick(*names):
        for n in names:
            if row is not None and n in row.index:
                v = _to_pct(row[n], is_ratio=False)
                if not np.isnan(v):
                    return v
        return np.nan
    return dict(
        eps_q  = pick('EPS Q/Q', 'EPS growthqtr over qtr', 'EPS Qtr Over Qtr'),
        eps_y  = pick('EPS this Y', 'EPS growththis year', 'EPS This Y'),
        eps_ny = pick('EPS next Y', 'EPS growthnext year'),
        sales_q= pick('Sales Q/Q', 'Sales growthqtr over qtr', 'Sales Qtr Over Qtr'),
        roe    = pick('ROE'),
        margin = pick('Profit Margin', 'Oper. Margin', 'Operating Margin'),
    )


def fund_from_yf(info):
    """yfinanceの.infoからファンダ指標を%で補完取得。
    yfの growth/ROE/margin はすべて『小数の比率』(0.25=25%)なので is_ratio=True。"""
    if not info:
        return {}
    def g(k):
        v = info.get(k)
        return _to_pct(v, is_ratio=True) if isinstance(v, (int, float)) else np.nan
    return dict(
        eps_q  = g('earningsQuarterlyGrowth'),
        eps_y  = np.nan,               # yfは当年EPS成長を直接持たない
        eps_ny = np.nan,
        sales_q= g('revenueGrowth'),
        roe    = g('returnOnEquity'),
        margin = g('profitMargins'),
    )


def fund_acceleration(tk):
    """成長の『加速』を判定(ミネルヴィニ/CAN SLIMが単なる高成長より重視する点)。
    yfinanceの四半期損益から、直近3四半期のEPS・売上の前年同期比成長率を出し、
    成長率自体が伸びているか(加速)を見る。
    returns dict(eps_accel, sales_accel, note) 各 True/False/None。"""
    out = dict(eps_accel=None, sales_accel=None, note="")
    try:
        qf = tk.quarterly_financials
        if qf is None or qf.empty:
            return out
        def yoy_series(rownames):
            row = None
            for rn in rownames:
                if rn in qf.index:
                    row = qf.loc[rn].dropna()
                    break
            if row is None or len(row) < 5:
                return None
            vals = list(row.values)[::-1]  # 古い→新しい
            yoy = []
            for i in range(4, len(vals)):
                prev = vals[i - 4]
                if prev and prev != 0:
                    yoy.append((vals[i] - prev) / abs(prev) * 100.0)
            return yoy[-3:] if len(yoy) >= 2 else None
        eps_yoy = yoy_series(['Diluted EPS', 'Basic EPS', 'Net Income'])
        sal_yoy = yoy_series(['Total Revenue', 'Operating Revenue'])
        def is_accel(seq):
            if not seq or len(seq) < 2:
                return None
            return seq[-1] > seq[0]  # 直近の成長率が数期前より高い
        out['eps_accel'] = is_accel(eps_yoy)
        out['sales_accel'] = is_accel(sal_yoy)
        parts = []
        if out['eps_accel'] is not None:
            parts.append("EPS加速" if out['eps_accel'] else "EPS減速")
        if out['sales_accel'] is not None:
            parts.append("売上加速" if out['sales_accel'] else "売上減速")
        out['note'] = " ".join(parts)
    except Exception:
        pass
    return out


def merge_fund(fv, yf_):
    """Finviz優先、欠損(nan)だけyfinanceで穴埋め。すべて%単位で統一済み。"""
    out = dict(fv)
    for k, v in (yf_ or {}).items():
        if k in out and (out[k] is None or (isinstance(out[k], float) and np.isnan(out[k]))):
            out[k] = v
    for k in ('eps_q', 'eps_y', 'eps_ny', 'sales_q', 'roe', 'margin'):
        out.setdefault(k, np.nan)
    return out


def score_fundamentals(f, accel=None):
    """ミネルヴィニ/CAN SLIM流のファンダ総合スコア(0-100)。すべて%単位の入力を前提。
    欠損は加点なし。成長の加速をボーナス配点する。returns dict(score, tags, detail)"""
    pts = 0.0
    tags = []

    # C: 直近四半期EPS成長(最重要, 28点)
    eq = f.get('eps_q', np.nan)
    if not np.isnan(eq):
        if eq >= EPS_Q_STRONG:       pts += 28; tags.append("EPS_Q◎")
        elif eq >= EPS_Q_STRONG*0.6: pts += 17; tags.append("EPS_Q○")
        elif eq > 0:                 pts += 8
        else:                        tags.append("EPS_Q✗")

    # A: 当年EPS成長(18点)
    ey = f.get('eps_y', np.nan)
    if not np.isnan(ey):
        if ey >= EPS_Y_STRONG:       pts += 18; tags.append("EPS_Y◎")
        elif ey >= EPS_Y_STRONG*0.6: pts += 11
        elif ey > 0:                 pts += 5
        else:                        tags.append("EPS_Y✗")

    # 売上四半期成長(18点)
    sq = f.get('sales_q', np.nan)
    if not np.isnan(sq):
        if sq >= SALES_Q_STRONG:       pts += 18; tags.append("売上Q◎")
        elif sq >= SALES_Q_STRONG*0.6: pts += 11; tags.append("売上Q○")
        elif sq > 0:                   pts += 5
        else:                          tags.append("売上Q✗")

    # ROE(13点)
    roe = f.get('roe', np.nan)
    if not np.isnan(roe):
        if roe >= ROE_STRONG:       pts += 13; tags.append("ROE◎")
        elif roe >= ROE_STRONG*0.6: pts += 7
        elif roe > 0:               pts += 3

    # 利益率がプラス(8点)
    mg = f.get('margin', np.nan)
    if not np.isnan(mg):
        if mg >= 10:   pts += 8; tags.append("利益率◎")
        elif mg > 0:   pts += 4
        else:          tags.append("赤字")

    # 来期EPS見通しがプラス(5点)
    eny = f.get('eps_ny', np.nan)
    if not np.isnan(eny) and eny > 0:
        pts += 5

    # ★成長の加速(合計10点のボーナス): CAN SLIMが重視する"伸びが伸びている"状態
    if accel:
        if accel.get('eps_accel') is True:
            pts += 6; tags.append("EPS加速")
        elif accel.get('eps_accel') is False:
            tags.append("EPS減速")
        if accel.get('sales_accel') is True:
            pts += 4; tags.append("売上加速")

    known = sum(1 for k in ('eps_q', 'eps_y', 'sales_q', 'roe', 'margin')
                if not np.isnan(f.get(k, np.nan)))
    if known == 0:
        return dict(score=np.nan, tags=["ファンダ不明"], detail=f)

    score = round(min(pts, 100))
    return dict(score=score, tags=tags[:5], detail=f)


def check_market():
    """地合い判定: S&P500(^GSPC)とNASDAQ(^IXIC)が自身の200日線より上か。
    両方上=良好 / 片方=中立 / 両方下=悪化。returns dict(state, label, detail)"""
    import yfinance as yf
    res = {}
    for sym, name in [('^GSPC', 'S&P500'), ('^IXIC', 'NASDAQ')]:
        try:
            h = with_retry(f"{name}({sym})の株価取得",
                           yf.download, sym, period="1y", interval="1d",
                           auto_adjust=True, progress=False,
                           validate=lambda d: d is not None and len(d) >= 200)
            if h is None or len(h) < 200:
                res[name] = None
                continue
            close = h['Close'].squeeze()
            ma200 = float(close.rolling(200).mean().iloc[-1])
            res[name] = bool(float(close.iloc[-1]) > ma200)
        except Exception:
            res[name] = None
    ups = [v for v in res.values() if v is True]
    downs = [v for v in res.values() if v is False]
    if len(ups) == 2:
        state, label = "good", "良好(主要指数が200日線の上)"
    elif len(downs) == 2:
        state, label = "bad", "悪化(主要指数が200日線の下)"
    else:
        state, label = "mixed", "中立(強弱まちまち)"
    detail = " / ".join(
        f"{k}:{'上' if v is True else ('下' if v is False else '不明')}"
        for k, v in res.items())
    return dict(state=state, label=label, detail=detail)


# ===== ここから実行 =====
def main(gc, sh):
    """スクリーニング本体。gc/sh は common.run_job から渡される。
    戻り値は実行ステータスタブに残すサマリー文字列。"""
    import yfinance as yf
    from finvizfinance.screener.overview import Overview
    from finvizfinance.screener.financial import Financial
    from finvizfinance.screener.valuation import Valuation
    import gspread
    import gspread_formatting as gsf

    print("1/6 出力先タブを準備中...")
    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(SHEET_NAME, rows=200, cols=26)

    # ★地合い(マーケット全体)チェック
    market = dict(state="unknown", label="未チェック", detail="")
    if CHECK_MARKET:
        print("1.5/6 地合い(S&P500/NASDAQ)を確認中...")
        try:
            market = check_market()
            print(f"  地合い: {market['label']} ({market['detail']})")
        except Exception as e:
            print(f"  [warn] 地合い判定に失敗: {str(e)[:50]}")
    market_penalty = 0.5 if (RESPECT_MARKET and market.get("state") == "bad") else 1.0
    if market_penalty < 1.0:
        print("  ※地合い悪化のため、株数目安を半分に保守化します")

    print("2/6 Finvizで一次選別中...")
    # ★ミネルヴィニ本来基準に近づけた業績フィルタを第1候補に。
    #   母集団が枯れたら段階的にテクニカルのみへフォールバックする。
    filter_sets = [
        {  # 第1候補: テクニカル + 本来基準に近い業績
            '200-Day Simple Moving Average': 'Price above SMA200',
            '50-Day Simple Moving Average': 'Price above SMA50',
            'Average Volume': 'Over 500K',
            'Price': 'Over $10',
            'EPS growthqtr over qtr': 'Over 25%',
            'Sales growthqtr over qtr': 'Over 20%',
        },
        {  # 第2候補: 業績を少し緩める
            '200-Day Simple Moving Average': 'Price above SMA200',
            '50-Day Simple Moving Average': 'Price above SMA50',
            'Average Volume': 'Over 500K',
            'Price': 'Over $10',
            'EPS growththis year': 'Positive (>0%)',
            'Sales growthqtr over qtr': 'Positive (>0%)',
        },
        {  # 第3候補: テクニカルのみ(業績は後段のファンダ採点で判定)
            '200-Day Simple Moving Average': 'Price above SMA200',
            '50-Day Simple Moving Average': 'Price above SMA50',
            'Average Volume': 'Over 500K',
            'Price': 'Over $10',
        },
    ]
    def fetch_finviz_overview():
        """フィルタ候補を上から順に試し、最初に結果が返ったものを採用する。
        全滅した場合は例外にする(Finvizにブロックされた場合を『本日は該当なし』と
        誤認しないため。第3候補はテクニカルのみなので、正常時にゼロ件はあり得ない)。"""
        errors = []
        for fs in filter_sets:
            try:
                fo = Overview()
                fo.set_filter(filters_dict=fs)
                df = fo.screener_view()
                if df is not None and not df.empty:
                    return df, fs
                errors.append("結果0件")
            except Exception as e:
                print(f"  [warn] フィルタ組み合わせが拒否されました({str(e)[:60]}) → 次の候補で再試行")
                errors.append(str(e)[:60])
                continue
        raise RuntimeError(
            "Finvizの一次選別が全フィルタ候補で失敗しました(レート制限/ブロックの疑い): "
            + " | ".join(errors))

    df_fv, used = with_retry("Finvizの一次選別", fetch_finviz_overview)

    if df_fv is None or df_fv.empty:
        tickers, meta = [], pd.DataFrame()
    else:
        n_total = len(df_fv)
        all_t = [str(t).strip().upper() for t in df_fv['Ticker'].tolist()]
        dup = [t for t in all_t if len(t) >= 2 and t[0] == t[1]]
        if all_t and len(dup) / len(all_t) > 0.8:
            print(f"  [warn] ティッカー先頭文字の重複を検出({len(dup)}/{len(all_t)})→ 先頭1文字を除去して補正します")
            fix_t = lambda t: t[1:] if len(t) >= 2 and t[0] == t[1] else t
            df_fv['Ticker'] = [fix_t(t) for t in all_t]
        else:
            df_fv['Ticker'] = all_t
        tickers = df_fv['Ticker'].tolist()[:MAX_DETAIL]
        meta = df_fv.set_index('Ticker')
        if n_total > MAX_DETAIL:
            print(f"  [info] Finviz該当は{n_total}銘柄でしたが、上位{MAX_DETAIL}銘柄に絞りました")
    print(f"  一次通過: {len(tickers)}銘柄")

    # ★ファンダ用: Finvizのfinancial/valuationビューも取得してティッカー索引で持つ。
    #   overviewと同じフィルタで引き、列を横結合する(追加のネット問い合わせは2回だけ)。
    fund_fv = pd.DataFrame()
    if tickers and used is not None:
        parts = []
        for View in (Financial, Valuation):
            def fetch_view(View=View):
                v = View()
                v.set_filter(filters_dict=used)
                return v.screener_view()
            try:
                dfp = with_retry(f"Finviz {View.__name__}ビューの取得", fetch_view)
                if dfp is not None and not dfp.empty:
                    dfp['Ticker'] = [str(t).strip().upper() for t in dfp['Ticker']]
                    # overview側でティッカー補正した場合に合わせる
                    if 'df_fv' in dir() and set(dfp['Ticker']) and \
                       len([t for t in dfp['Ticker'] if len(t) >= 2 and t[0] == t[1]]) / max(len(dfp), 1) > 0.8:
                        dfp['Ticker'] = [t[1:] if len(t) >= 2 and t[0] == t[1] else t for t in dfp['Ticker']]
                    parts.append(dfp.set_index('Ticker'))
            except Exception as e:
                print(f"  [warn] {View.__name__}ビュー取得失敗({str(e)[:50]}) → yfinanceで補完します")
        if parts:
            fund_fv = pd.concat(parts, axis=1)
            fund_fv = fund_fv.loc[:, ~fund_fv.columns.duplicated()]

    rows = []
    _d = datetime.date.today()
    today = f'=DATE({_d.year},{_d.month},{_d.day})'

    if tickers:
        print("3/6 株価をまとめ取り中(1回で全銘柄)...")
        data = with_retry("yfinanceでの株価一括取得",
                          yf.download, tickers, period="2y", interval="1d",
                          auto_adjust=True, group_by="ticker", progress=False,
                          threads=True,
                          validate=lambda d: d is not None and not d.empty)

        def one(t):
            d = data[t] if isinstance(data.columns, pd.MultiIndex) else data
            return d.dropna(subset=["Close"])

        n_ok = 0
        for t in tickers:
            try:
                if len(one(t)) > 0:
                    n_ok += 1
            except Exception:
                pass
        if n_ok < len(tickers) * 0.5:
            raise RuntimeError(
                f"株価取得の成功が{n_ok}/{len(tickers)}銘柄のみ。ティッカーが壊れている可能性が高いので中断します")

        wr = {}
        for t in tickers:
            try:
                d = one(t)
                if len(d) >= 260:
                    wr[t] = weighted_return(d["Close"])
            except Exception:
                pass
        rs_rank = pd.Series(wr).rank(pct=True).mul(98).add(1).round()

        print(f"4/6 テクニカル精密判定中({len(wr)}銘柄)...")
        # テクニカルを通過した銘柄だけを集め、そのあとファンダ取得する(yf.info呼び出し節約)
        tech_pass = []
        for t in tickers:
            try:
                d = one(t)
                if len(d) < 260:
                    continue
                c = float(d["Close"].iloc[-1])
                ma50 = d["Close"].rolling(50).mean()
                ma150 = d["Close"].rolling(150).mean()
                ma200 = d["Close"].rolling(200).mean()
                lo52 = float(d["Low"].rolling(252).min().iloc[-1])
                hi52 = float(d["High"].rolling(252).max().iloc[-1])
                rs = int(rs_rank.get(t, 0))

                tt = [
                    c > ma150.iloc[-1] and c > ma200.iloc[-1],
                    ma150.iloc[-1] > ma200.iloc[-1],
                    ma200.iloc[-1] > ma200.iloc[-22],
                    ma50.iloc[-1] > ma150.iloc[-1] > ma200.iloc[-1],
                    c > ma50.iloc[-1],
                    c >= lo52 * 1.30,
                    c >= hi52 * 0.75,
                    rs >= 70,
                ]
                if not all(tt):
                    continue
                tech_pass.append((t, d, c, ma200, hi52, rs, float(ma50.iloc[-1])))
            except Exception:
                continue

        print(f"5/6 ファンダ分析中({len(tech_pass)}銘柄)...")
        for t, d, c, ma200, hi52, rs, ma50_last in tech_pass:
            try:
                v = detect_vcp(d)
                stage = classify_stage(d)
                pivot = v["pivot"] if v["valid"] else round(float(d["High"].iloc[-20:].max()), 2)
                act = triage(c, pivot, v["valid"], hi52=hi52, ma50=ma50_last)
                stop = v["stop"] if v["valid"] else round(pivot * 0.92, 2)
                risk_ps = max(pivot * 1.001 - stop, 0.01)
                shares = int(EQUITY * 0.0075 / risk_ps)
                shares = min(shares, int(EQUITY * 0.25 / max(pivot, 0.01)))
                dev = (c - pivot) / pivot
                trend_strength = (c - float(ma200.iloc[-1])) / float(ma200.iloc[-1])
                # ★50日線からの乖離率(EXTENDED判定に使っている過熱度を、数値として可視化)
                ma50_dev = (c - ma50_last) / ma50_last if ma50_last else np.nan

                # --- ファンダ取得: Finviz優先 → 欠損はyfinance補完(すべて%単位に正規化済み) ---
                fv_row = fund_fv.loc[t] if (not fund_fv.empty and t in fund_fv.index) else None
                f_fv = fund_from_finviz(fv_row)
                need_yf = any(np.isnan(f_fv.get(k, np.nan))
                              for k in ('eps_q', 'sales_q', 'roe', 'margin'))
                tk = None
                f_yf = {}
                if need_yf:
                    try:
                        tk = yf.Ticker(t)
                        f_yf = fund_from_yf(tk.info)
                    except Exception:
                        f_yf = {}
                fund = merge_fund(f_fv, f_yf)

                # ★成長の加速を判定(四半期損益が必要なのでyf.Tickerを使う)
                try:
                    if tk is None:
                        tk = yf.Ticker(t)
                    accel = fund_acceleration(tk)
                except Exception:
                    accel = None

                fs_res = score_fundamentals(fund, accel=accel)
                fscore = fs_res["score"]
                ftags = " ".join(fs_res["tags"])

                # ★ファンダ足切り: スコアが基準未満なら除外(判定不能=nanは残す)
                if not (isinstance(fscore, float) and np.isnan(fscore)):
                    if fscore < FUND_MIN_SCORE:
                        continue

                def pctval(x):
                    """%の数値(例66.9)を、PERCENT書式で正しく表示されるよう小数比率(0.669)にする。
                    欠損は空欄。これで『数字だけで単位不明』も『桁ずれ』も同時に解消する。"""
                    if x is None or (isinstance(x, float) and np.isnan(x)):
                        return ""
                    return round(x / 100.0, 4)

                comp = str(meta.at[t, 'Company']) if 'Company' in meta.columns else t
                comp = comp.replace('"', '""')
                sec = str(meta.at[t, 'Sector']) if 'Sector' in meta.columns else ''
                ind = str(meta.at[t, 'Industry']) if 'Industry' in meta.columns else ''
                si = f"{sec} - {ind}".replace('"', '""')

                hist100 = d["Close"].iloc[-100:]
                color = "#089981" if c >= float(hist100.iloc[0]) else "#F23645"

                # 総合ソートキー: ファンダ7割 + テクニカル(VCPスコア)3割
                fscore_num = 0 if (isinstance(fscore, float) and np.isnan(fscore)) else fscore
                combo = round(fscore_num * 0.7 + v["score"] * 0.3, 1)

                # 株数目安に地合いペナルティを反映
                shares = int(shares * market_penalty)

                rows.append([
                    today,                     # A 更新日
                    f'=HYPERLINK("https://finviz.com/quote.ashx?t={t}", "{t}")',  # B ティッカー
                    f'=IFERROR(GOOGLETRANSLATE("{comp}", "en", "ja"), "{comp}")', # C 企業名
                    f'=IFERROR(GOOGLETRANSLATE("{si}", "en", "ja"), "{si}")',     # D 業態
                    f'=SPARKLINE(GOOGLEFINANCE("{t}", "price", TODAY()-100, TODAY()), '
                    f'{{"charttype","line";"color","{color}";"linewidth",2}})',   # E チャート
                    act,                       # F 判定
                    stage,                     # G ステージ
                    round(c, 2),               # H 現在値
                    pivot,                     # I 買いポイント
                    round(dev, 4),             # J 乖離率
                    round(ma50_dev, 4) if not (isinstance(ma50_dev, float) and np.isnan(ma50_dev)) else "",  # K 50日線乖離率
                    v["grade"],                # L VCP
                    v["footprint"],            # M 形
                    v["score"],                # N VCPスコア
                    combo,                     # O 総合スコア
                    rs,                        # P 相対強度
                    "" if (isinstance(fscore, float) and np.isnan(fscore)) else int(fscore),  # Q ファンダ点
                    pctval(fund.get('eps_q')),    # R EPS成長Q(前年比)
                    pctval(fund.get('sales_q')),  # S 売上成長Q(前年比)
                    pctval(fund.get('roe')),      # T ROE
                    pctval(fund.get('margin')),   # U 利益率
                    ftags,                     # V ファンダ根拠
                    stop,                      # W 逆指値目安
                    shares,                    # X 株数目安
                    round(trend_strength, 4),  # Y トレンド強度(200日線乖離)
                ])
            except Exception:
                continue

    print("6/6 スプレッドシートへ書き込み中...")
    headers = ["更新日", "ティッカー(Finvizへ)", "企業名", "業態", "100日チャート",
               "判定", "ステージ", "現在値($)", "買いポイント($)", "乖離率(%)", "50日線乖離率(%)",
               "VCP", "形", "VCPスコア", "総合スコア", "相対強度", "ファンダ点",
               "EPS成長Q(前年比%)", "売上成長Q(前年比%)", "ROE(%)", "利益率(%)", "ファンダ根拠",
               "逆指値目安($)", "株数目安", "トレンド強度(%)"]
    # 並び順: 判定(買い場の近さ) → 総合スコア降順 → ステージ2初期優先
    order = {"BREAKOUT": 0, "ALERT": 1, "WATCH": 2, "EXTENDED": 3, "形成中": 4}
    rows.sort(key=lambda r: (order.get(r[5], 9), -r[14], 0 if r[6] == "2初期" else 1))

    ws.clear()
    if rows:
        all_data = [headers] + rows
        ws.update(range_name='A1', values=all_data, value_input_option='USER_ENTERED')
        end = len(all_data)
        ncol = len(headers)

        sh.batch_update({"requests": [{"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": end + 50,
                      "startColumnIndex": 0, "endColumnIndex": ncol},
            "cell": {"userEnteredFormat": {}},
            "fields": "userEnteredFormat.numberFormat"}}]})

        dt  = gsf.CellFormat(numberFormat=gsf.NumberFormat(type='DATE', pattern='yyyy/mm/dd'))
        cur = gsf.CellFormat(numberFormat=gsf.NumberFormat(type='CURRENCY', pattern='"$"#,##0.00'))
        pct = gsf.CellFormat(numberFormat=gsf.NumberFormat(type='PERCENT', pattern='0.00%'))
        num = gsf.CellFormat(numberFormat=gsf.NumberFormat(type='NUMBER', pattern='0'))
        gsf.format_cell_range(ws, f'A2:A{end}', dt)    # 更新日
        gsf.format_cell_range(ws, f'H2:I{end}', cur)   # 現在値・買いポイント
        gsf.format_cell_range(ws, f'W2:W{end}', cur)   # 逆指値目安
        gsf.format_cell_range(ws, f'J2:K{end}', pct)   # 乖離率・50日線乖離率
        gsf.format_cell_range(ws, f'Y2:Y{end}', pct)   # トレンド強度
        # ★ファンダ列を%表示に。成長率(R,S)は符号付きで増減を明示、ROE/利益率(T,U)は通常%
        pct_signed = gsf.CellFormat(numberFormat=gsf.NumberFormat(type='PERCENT', pattern='+0.0%;-0.0%'))
        gsf.format_cell_range(ws, f'R2:S{end}', pct_signed)  # EPS成長Q・売上成長Q
        gsf.format_cell_range(ws, f'T2:U{end}', pct)         # ROE・利益率
        gsf.format_cell_range(ws, f'N2:N{end}', num)   # VCPスコア
        gsf.format_cell_range(ws, f'P2:Q{end}', num)   # 相対強度・ファンダ点
        gsf.format_cell_range(ws, f'O2:O{end}', gsf.CellFormat(
            numberFormat=gsf.NumberFormat(type='NUMBER', pattern='0.0')))  # 総合スコア
        gsf.format_cell_range(ws, f'X2:X{end}', gsf.CellFormat(
            numberFormat=gsf.NumberFormat(type='NUMBER', pattern='#,##0')))  # 株数目安

        # 条件付き書式
        rule_pivot = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'J2:J{end}', ws)],
            booleanRule=gsf.BooleanRule(
                condition=gsf.BooleanCondition('NUMBER_BETWEEN', ['-0.05', '0.05']),
                format=gsf.CellFormat(backgroundColor=gsf.Color(0.85, 0.96, 0.82))))
        rule_score = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'N2:N{end}', ws)],   # VCPスコア
            gradientRule=gsf.GradientRule(
                minpoint=gsf.InterpolationPoint(color=gsf.Color(1, 1, 1), type='MIN'),
                maxpoint=gsf.InterpolationPoint(color=gsf.Color(0.6, 0.8, 1.0), type='MAX')))
        # ★ファンダ点・総合スコアもヒートマップ
        rule_fund = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'Q2:Q{end}', ws)],   # ファンダ点
            gradientRule=gsf.GradientRule(
                minpoint=gsf.InterpolationPoint(color=gsf.Color(1, 1, 1), type='MIN'),
                maxpoint=gsf.InterpolationPoint(color=gsf.Color(0.7, 0.9, 0.7), type='MAX')))
        rule_combo = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'O2:O{end}', ws)],   # 総合スコア
            gradientRule=gsf.GradientRule(
                minpoint=gsf.InterpolationPoint(color=gsf.Color(1, 1, 1), type='MIN'),
                maxpoint=gsf.InterpolationPoint(color=gsf.Color(0.55, 0.75, 1.0), type='MAX')))
        rule_vcp_a = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'L2:L{end}', ws)],   # VCPグレード
            booleanRule=gsf.BooleanRule(
                condition=gsf.BooleanCondition('TEXT_EQ', ['A']),
                format=gsf.CellFormat(textFormat=gsf.TextFormat(
                    bold=True, foregroundColor=gsf.Color(0.0, 0.45, 0.15)))))
        rule_vcp_b = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'L2:L{end}', ws)],
            booleanRule=gsf.BooleanRule(
                condition=gsf.BooleanCondition('TEXT_EQ', ['B']),
                format=gsf.CellFormat(textFormat=gsf.TextFormat(
                    foregroundColor=gsf.Color(0.15, 0.55, 0.25)))))
        rule_vcp_c = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'L2:L{end}', ws)],
            booleanRule=gsf.BooleanRule(
                condition=gsf.BooleanCondition('TEXT_EQ', ['C']),
                format=gsf.CellFormat(textFormat=gsf.TextFormat(
                    foregroundColor=gsf.Color(0.55, 0.55, 0.4)))))
        rule_stage = gsf.ConditionalFormatRule(
            ranges=[gsf.GridRange.from_a1_range(f'G2:G{end}', ws)],
            booleanRule=gsf.BooleanRule(
                condition=gsf.BooleanCondition('TEXT_EQ', ['2初期']),
                format=gsf.CellFormat(backgroundColor=gsf.Color(1.0, 0.87, 0.35),
                                      textFormat=gsf.TextFormat(bold=True))))
        rules = gsf.get_conditional_format_rules(ws)
        rules.clear()
        for r_ in [rule_pivot, rule_score, rule_fund, rule_combo,
                   rule_vcp_a, rule_vcp_b, rule_vcp_c, rule_stage]:
            rules.append(r_)
        rules.save()

        ROW_COLOR = {
            "BREAKOUT": (0.87, 0.82, 0.97),
            "ALERT":    (0.80, 0.94, 0.80),
            "WATCH":    (1.00, 0.97, 0.78),
            "EXTENDED": (0.99, 0.87, 0.78),
        }
        last_col = "Y"
        for act, rgb in ROW_COLOR.items():
            idxs = [i + 2 for i, r_ in enumerate(rows) if r_[5] == act]
            if not idxs:
                continue
            ranges = [f"A{i}:{last_col}{i}" for i in idxs]
            fmt_bg = {"backgroundColor": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}
            try:
                ws.format(ranges, fmt_bg)
            except Exception:
                for rg in ranges:
                    ws.format(rg, fmt_bg)

        gsf.set_column_width(ws, 'E', 150)
        gsf.set_column_width(ws, 'V', 160)   # ファンダ根拠
        gsf.set_row_height(ws, f'2:{end}', 35)
        ws.freeze(rows=1)

        # ★地合いをシート上部(ヘッダー右の空きセル)に注記
        if CHECK_MARKET and market.get("state") != "unknown":
            mcolor = {"good": (0.80, 0.94, 0.80), "bad": (0.99, 0.80, 0.80),
                      "mixed": (1.0, 0.97, 0.78)}.get(market["state"], (1, 1, 1))
            try:
                ws.update(range_name='AA1',
                          values=[[f"地合い: {market['label']}  ({market['detail']})"]],
                          value_input_option='USER_ENTERED')
                ws.format('AA1', {"backgroundColor": {"red": mcolor[0], "green": mcolor[1], "blue": mcolor[2]},
                                  "textFormat": {"bold": True}})
            except Exception:
                pass

        alerts = [r for r in rows if r[5] in ("ALERT", "BREAKOUT")]
        if alerts:
            syms = [r[1].split('t=')[1].split('"')[0] for r in alerts]
            try:
                with open(ALERTS_OUTPUT_PATH, "w") as fp:
                    fp.write(",".join(syms))
                print(f"  要注目 {len(alerts)}銘柄 → {ALERTS_OUTPUT_PATH} に書き出し(TVインポート用)")
            except OSError as e:
                # Cloud Run のコンテナは実行のたびに破棄されるので、書けなくても処理は続ける
                print(f"  [warn] 要注目リストの書き出しに失敗: {str(e)[:80]}")

        n_bo = sum(1 for r in rows if r[5] == "BREAKOUT")
        n_al = sum(1 for r in rows if r[5] == "ALERT")
        n_s2 = sum(1 for r in rows if r[6] == "2初期")
        n_ff = sum(1 for r in rows if isinstance(r[16], int) and r[16] >= 70)
        n_ac = sum(1 for r in rows if "加速" in str(r[21]))
        print(f"【成功】{len(rows)}銘柄を書き込みました(ファンダ足切り{FUND_MIN_SCORE}点適用済み)")
        if CHECK_MARKET:
            print(f"  地合い: {market['label']}")
        print(f"  BREAKOUT: {n_bo}銘柄 / ALERT: {n_al}銘柄 / ステージ2初期: {n_s2}銘柄")
        print(f"  ファンダ点70以上(業績も強い): {n_ff}銘柄 / 成長加速: {n_ac}銘柄")
        print("  行の色: 紫=新高値更新中 / 緑=買い場接近 / 黄=監視 / 橙=伸びすぎ / 無色=形成中")
        print(f"→ スプレッドシート「{SPREADSHEET_NAME}」の「{SHEET_NAME}」タブを開いてください")
        return (f"{len(rows)}銘柄 / BREAKOUT:{n_bo} ALERT:{n_al} ステージ2初期:{n_s2}"
                f" / 地合い:{market.get('label', '未チェック')}")
    else:
        ws.update(range_name='A1',
                  values=[headers, [today, "本日は全条件を満たす銘柄なし(地合いが悪い時期は正常)"] + [""] * (len(headers) - 2)])
        print("【完了】本日は条件を満たす銘柄がありませんでした(シートに記録済み)")
        return f"0銘柄(条件を満たす銘柄なし) / 地合い:{market.get('label', '未チェック')}"


if __name__ == "__main__":
    sys.exit(run_job(JOB_NAME, SPREADSHEET_NAME, main))
