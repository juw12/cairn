#!/bin/bash
# count.sh LABEL : print the menu bar's icon count now and for the next seconds
python="$HOME/Library/Application Support/Cairn/venv/bin/python"
for second in 0 3 6 9; do
  echo "$1 +${second}s: $("$python" scripts/mac_check.py --menu-bar) icons"
  sleep 3
done
