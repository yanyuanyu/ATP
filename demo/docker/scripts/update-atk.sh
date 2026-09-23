#!/bin/bash
# Extracts each server's SM2 public key and writes it into the
# corresponding BIND9 zone file, then reloads BIND9.
set -euo pipefail
cd "$(dirname "$0")/.."

ZONE_DIR="$(pwd)/bind/zones"

# Portable in-place sed (GNU sed -i and BSD sed -i '' differ; .bak + rm works on both).
patch_zone() {
  local zone="$1"
  local pubkey="$2"
  test -n "$pubkey" || { echo "Missing SM2 public key" >&2; exit 1; }
  sed -i.bak "s|^default.atk._atp.*|default.atk._atp IN TXT \"v=atp1 k=sm2 p=${pubkey}\"|" "$zone"
  rm -f "$zone.bak"
}

PUBKEY_FAMILY=$(docker compose exec -T server-family \
  atp keys show --selector default --algorithm sm2 --public \
  | grep 'Public key' | awk '{print $NF}')
patch_zone "$ZONE_DIR/family.test.zone" "$PUBKEY_FAMILY"
echo "  family.test  ATK pubkey: ${PUBKEY_FAMILY:-<empty>}"

PUBKEY_HOTEL=$(docker compose exec -T server-hotel \
  atp keys show --selector default --algorithm sm2 --public \
  | grep 'Public key' | awk '{print $NF}')
patch_zone "$ZONE_DIR/hotel.test.zone" "$PUBKEY_HOTEL"
echo "  hotel.test   ATK pubkey: ${PUBKEY_HOTEL:-<empty>}"

PUBKEY_PAYMENT=$(docker compose exec -T server-payment \
  atp keys show --selector default --algorithm sm2 --public \
  | grep 'Public key' | awk '{print $NF}')
patch_zone "$ZONE_DIR/payment.test.zone" "$PUBKEY_PAYMENT"
echo "  payment.test ATK pubkey: ${PUBKEY_PAYMENT:-<empty>}"

docker compose restart dns
echo "ATK records updated and DNS restarted."
