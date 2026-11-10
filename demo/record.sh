#!/bin/bash
# Record one take of the demo: Xvfb display, kitty terminal, real Hermes session driven by drive.py.
# Usage: demo/record.sh <out_dir>   (needs HERMES_HOME with the plugin enabled and a model key)
set -euo pipefail
OUT=${1:?out dir}; mkdir -p "$OUT/work"
HERE=$(cd "$(dirname "$0")" && pwd)
DISP=${DISP:-:77}
export DISPLAY=$DISP
pgrep -f "Xvfb $DISP" >/dev/null || { Xvfb $DISP -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 & sleep 1.5; }
ffmpeg -loglevel error -y -f x11grab -framerate 30 -video_size 1280x720 -i $DISP \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p "$OUT/raw.mp4" < /dev/null &
FF=$!
date +%s.%N > "$OUT/ffmpeg_t0"
cat > "$OUT/inner.sh" <<INNER
#!/bin/bash
cd "$OUT/work"
export PS1='\[\e[1;32m\]demo\[\e[0m\] \$ ' TERM=xterm-256color HERMES_HOME="$HERMES_HOME" PATH="$PATH"
DRIVE_MARKS="$OUT/marks.json" python3 "$HERE/drive.py" "$HERE/demo.json" "$OUT/session.log" 2>"$OUT/drive.err"
INNER
chmod +x "$OUT/inner.sh"
LIBGL_ALWAYS_SOFTWARE=1 kitty --config NONE -o font_family="JetBrains Mono" -o font_size=14 \
  -o background=#0f1115 -o foreground=#e6e6e6 -o remember_window_size=no \
  -o initial_window_width=1280 -o initial_window_height=640 -o window_padding_width=10 \
  -o cursor_blink_interval=0 -o enable_audio_bell=no -o placement_strategy=top-left \
  -o hide_window_decorations=yes "$OUT/inner.sh" >"$OUT/kitty.err" 2>&1 < /dev/null
sleep 0.5
kill -INT $FF; wait $FF || true
echo "recorded $OUT/raw.mp4"
