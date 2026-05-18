#!/usr/bin/env bash
# 외부 음원 호스트 접근 불가 환경용 BGM placeholder 생성기.
# 7개 mood 별로 ffmpeg sine wave 합성 → 15s mp3.
# 회원님이 실제 royalty-free 음원 받으면 같은 파일명으로 덮어쓰기만 하면 됨.
#
# Mood 매핑 (caption_to_spec.py 가 LLM 으로 분류):
#   gentle     — 공감/따뜻 (메이저 코드, 부드러운 패드)
#   calm       — 정보 전달 (서스테인된 단음)
#   serious    — 페인포인트/위기감 (마이너 코드, 낮은 음역)
#   uplifting  — 희망/긍정 (메이저 코드 + 5도)
#   focused    — 전문가 톤 (미니멀, 거의 들리지 않는 톤)
#   warm       — 인간미 (낮은 메이저 패드)
#   subtle     — 배경용 (3도 화음 부드러움)
set -e
cd "$(dirname "$0")"

DUR=15
SR=44100

# ─ 공통 envelope: 2s fade in + 2s fade out + 낮은 볼륨 0.25
ENV="afade=t=in:st=0:d=2,afade=t=out:st=$((DUR-2)):d=2,volume=0.25,lowpass=f=1500"

mkgen() {
  local name=$1; shift
  local freqs=("$@")
  local inputs=""
  local mix_inputs=""
  local i=0
  for f in "${freqs[@]}"; do
    inputs="$inputs -f lavfi -i sine=frequency=$f:sample_rate=$SR:duration=$DUR"
    mix_inputs="$mix_inputs[$i:a]"
    i=$((i+1))
  done
  local filter="${mix_inputs}amix=inputs=${#freqs[@]}:duration=longest,$ENV"
  ffmpeg -y -loglevel error $inputs \
         -filter_complex "$filter" \
         -c:a libmp3lame -b:a 128k -ac 2 "track_${name}.mp3"
  echo "  generated track_${name}.mp3 ($(du -h track_${name}.mp3 | cut -f1))"
}

# C major (gentle): C4 E4 G4
mkgen gentle    261.63 329.63 392.00
# A3 sustained (calm): single warm note
mkgen calm      220.00 329.63
# C minor (serious): C3 Eb3 G3 — lower octave for weight
mkgen serious   130.81 155.56 196.00
# C major + 5th (uplifting): C4 E4 G4 C5
mkgen uplifting 261.63 329.63 392.00 523.25
# Single low note (focused): A2 minimal
mkgen focused   110.00
# F major low (warm): F3 A3 C4
mkgen warm      174.61 220.00 261.63
# Third interval (subtle): D4 F4 only
mkgen subtle    293.66 349.23
