#!/bin/bash
# StockBarks 安裝腳本
set -e

DEST="$HOME/.hermes/swiftbar"
SECRETS="$HOME/.hermes/secrets"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> 建立目錄"
mkdir -p "$DEST" "$SECRETS"

echo "==> 複製 swiftbar 程式"
cp "$SCRIPT_DIR/swiftbar/"*.py "$DEST/"
cp "$SCRIPT_DIR/swiftbar/"*.sh "$DEST/"
chmod +x "$DEST/"*.py "$DEST/"*.sh

echo "==> 套用預設 watchlist (若已存在則保留)"
[ -f "$DEST/stockwatch_state.json" ] || cp "$SCRIPT_DIR/examples/stockwatch_state.example.json" "$DEST/stockwatch_state.json"

echo "==> 清除舊 tick 緩存"
rm -f "$DEST/stockwatch_ticks.json"

echo "==> 檢查 Shioaji 憑證"
if [ ! -f "$SECRETS/sinotrade.env" ]; then
  echo ""
  echo "  缺少 $SECRETS/sinotrade.env"
  echo "  請從 examples/sinotrade.env.example 複製並填入你的憑證："
  echo ""
  echo "  cp $SCRIPT_DIR/examples/sinotrade.env.example $SECRETS/sinotrade.env"
  echo "  vim $SECRETS/sinotrade.env"
  echo ""
fi
if [ ! -f "$SECRETS/Sinopac.pfx" ]; then
  echo "  缺少 $SECRETS/Sinopac.pfx — 請從永豐金證券下載你的 CA 簽章檔"
fi

echo "==> 安裝 Python 依賴"
/usr/bin/python3 -c "import PIL" 2>/dev/null || /usr/bin/python3 -m pip install --user pillow

SHIOAJI_PY="/Applications/Xcode-beta.app/Contents/Developer/usr/bin/python3"
[ -x "$SHIOAJI_PY" ] || SHIOAJI_PY="/Applications/Xcode.app/Contents/Developer/usr/bin/python3"
[ -x "$SHIOAJI_PY" ] || SHIOAJI_PY="/usr/bin/python3"
$SHIOAJI_PY -c "import shioaji" 2>/dev/null || {
  echo "  安裝 Shioaji..."
  $SHIOAJI_PY -m pip install --user shioaji pandas
}

echo "==> 檢查 SwiftBar"
if ! [ -d "/Applications/SwiftBar.app" ]; then
  echo ""
  echo "  尚未安裝 SwiftBar，請執行："
  echo "    brew install --cask swiftbar"
  echo "  首次啟動時把 Plugin Directory 設為： $DEST"
fi

echo "==> 啟動 daemon"
if [ -f "$SECRETS/sinotrade.env" ]; then
  bash "$DEST/stockwatch_daemon_restart.sh" || true
else
  echo "  跳過 daemon 啟動（請先設定 sinotrade.env）"
fi

echo ""
echo "完成。SwiftBar menu bar 會顯示即時股價 + 分時走勢。"
echo "點圖示 → Switch / Add 可切換或新增股票。"
