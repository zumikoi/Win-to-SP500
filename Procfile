# buildpacks がビルド時にエントリポイントを要求するためのファイル。
# 実行時は Cloud Run Job 側の --command / --args で上書きされるため、
# ここでどのジョブを指していても動作には影響しない。
web: python -m screeners.minervini_screener
