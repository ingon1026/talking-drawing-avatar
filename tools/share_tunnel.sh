#!/usr/bin/env bash
# 로컬 아바타 서버(:8000)를 공개 URL로 노출 — 데모를 남에게 보여줄 때만 켠다.
# Cloudflare quick tunnel: 계정·비용 없음, 대신 실행마다 URL이 새로 생기고 이 창을 닫으면 종료.
#
# 사용: bash tools/share_tunnel.sh   → 출력되는 https://…​.trycloudflare.com 링크를 공유
# 종료: Ctrl+C
#
# 주의(공개 노출이므로):
#  - 이 링크는 아는 사람에게만. 인증이 없어 누구나 발화·대화·캐릭터 생성이 가능하다.
#  - 내 PC와 서버(systemctl --user status face-avatar)가 켜져 있을 때만 접속된다.
#  - 첫 3D(/3d) 접속은 41MB GLB 다운로드라 터널을 통하면 수십 초 걸릴 수 있다.
set -e

if ! curl -sf -m 3 localhost:8000/api/health >/dev/null; then
  echo "⚠️  로컬 서버가 꺼져 있습니다. 먼저: systemctl --user start face-avatar" >&2
  exit 1
fi

echo "🌐 공개 터널을 엽니다… (Ctrl+C 로 종료)"
echo "   아래 https://…​.trycloudflare.com/puppet 링크를 공유하세요."
echo
exec "$HOME/.local/bin/cloudflared" tunnel --url http://localhost:8000
